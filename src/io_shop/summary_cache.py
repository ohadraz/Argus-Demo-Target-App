from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from dataclasses import dataclass

"""The spend figure Io has already worked out once, kept so it need not again.

Working the figure out means walking a shopper's whole purchase history, and an
account page is the most-visited page the shop has. So the figure is cached
under the shopper it belongs to, and the page reads the cache before it
computes - which is what makes the shop fast rather than merely correct.

Nothing here fails a page. A cache that has nothing for this shopper, a cache
that cannot be reached at all, and a cache that is too slow to be worth waiting
for all end the same way: the page works the figure out for itself and renders.
That is the designed behaviour and it is load-bearing - a shop that failed when
its cache did would be a shop whose cache is a dependency rather than an
optimisation, and losing it would be an outage instead of a slowdown.

Which is also why losing it is so easy to miss. Every page is still correct.
The only thing that changes is how long each one takes.

And that is why the wait is bounded. A cache that refuses a connection hands
the page straight back to the fallback; a cache that simply takes a second and
a half to answer never does, and the shop inherits its latency one request for
one. An optimisation the page waits on without limit has stopped being an
optimisation - so the shop gives it a budget, and spends the fallback rather
than the time.

Where the cache lives is not this module's to know. The endpoint is deployment
configuration, handed in by whoever is running the shop, and how it is reached
is a seam the caller supplies.
"""

# How the cache is addressed, in the scheme its client speaks. Spelled out so
# that the endpoint in a failure line is the endpoint the shop actually dialled
# - a reader comparing it against what the configuration says is doing the one
# comparison that diagnoses this.
_ENDPOINT_FORMAT = "redis://{host}:{port}"

# How long the page will wait for the cache before working the figure out
# itself. Generous for a lookup that normally answers in single-digit
# milliseconds, and well inside what the page as a whole is expected to take -
# the point of the number is that it is a number, not that it is this one. An
# unbounded wait is the only setting that turns somebody else's slowness into
# all of ours.
HOW_LONG_THE_SHOP_WAITS_SECONDS = 0.1

# How many renders may be waiting on the cache at once. The waiting is done on
# a shared pool rather than a thread per render, for the reason the seam itself
# exists: the shop is rendered many times over to produce a minute of
# telemetry. A render that finds the pool full is a render that waits out its
# budget and falls back, which is the same degradation as a slow answer and the
# right one - nothing queues behind an upstream that has stopped answering.
_MOST_RENDERS_WAITING = 32

_WAITING_ON_THE_CACHE = concurrent.futures.ThreadPoolExecutor(
    max_workers=_MOST_RENDERS_WAITING, thread_name_prefix="summary-cache"
)


@dataclass(frozen=True)
class CacheEndpoint:
    """Where the cache is, as the deployment configured it.

    A value rather than a pair of loose strings, because the two are only an
    address together and because the address is what a failure has to name. It
    carries no client and opens nothing: this says where, and the seam says how.
    """

    host: str
    port: int

    def __str__(self) -> str:
        return _ENDPOINT_FORMAT.format(host=self.host, port=self.port)


@dataclass(frozen=True)
class CacheAnswer:
    """One reply from the cache: whether it was reached, and what it held.

    The two are separate because they mean different things to the shop even
    though both end in a recomputation. A cache that answered and held nothing
    is a cache working normally - every entry has a first request. A cache that
    could not be reached is the shop's fast path gone, for every shopper at
    once, and it is worth saying so.

    Whether it was reached is carried rather than interpreted by whoever
    fetched it, for the reason the provider's status is: turning it into the
    shop's own words is this module's job, and a fetcher that composed them
    would put that in as many places as there are ways to reach a cache.
    """

    reached: bool
    summary_cents: int | None = None


class CacheUnreachable(Exception):
    """The cache could not be reached at all.

    Raised so that the caller has to decide what to do about it, and caught
    immediately by the one caller there is - which sounds pointless and is not.
    A miss returns `None` and is unremarkable; this is a different fact about
    the world, and a shop that returned `None` for both would have no way to
    say which of them it is living through.
    """


class CacheTooSlow(CacheUnreachable):
    """The cache did not answer inside the time the page had for it.

    A kind of unreachable rather than a thing of its own, because that is what
    it is to the page: there is no figure to be had here in time, and the
    fallback is the same fallback. Saying so in its own type is what lets a
    reader tell the two apart in a log - a refused connection and an answer
    that never arrived have the same effect on a render and completely
    different causes on the other end of the socket.
    """


# How the cache is reached, given a shopper. A seam rather than a client, for
# the reason the payment provider's is one: the shop is rendered many times over
# to produce a minute of telemetry, and a connection per render would be
# thousands of them per read.
type LookUpSummary = Callable[[str], CacheAnswer]


def cached_summary(shopper_id: str,
                   look_up: LookUpSummary,
                   endpoint: CacheEndpoint,
                   patience_seconds: float = HOW_LONG_THE_SHOP_WAITS_SECONDS
                   ) -> int | None:
    """The figure the cache holds for this shopper, or `None` where it holds
    none.

    Raises `CacheUnreachable` when the cache did not answer, and the message is
    the point of the function: it names the endpoint the shop dialled, port
    included. A failure that said only "cache unavailable" would leave a reader
    knowing the cache is gone and unable to work out why - where the endpoint,
    set against the endpoint the configuration was supposed to carry, is the
    whole diagnosis.

    Raises `CacheTooSlow` - which is a `CacheUnreachable` - when the answer did
    not arrive inside `patience_seconds`. The page has a figure it can work out
    for itself, so waiting longer buys nothing except the upstream's latency on
    every request that renders.
    """
    answer = _answer_within(shopper_id, look_up, endpoint, patience_seconds)

    if not answer.reached:
        raise CacheUnreachable(f"connection refused to {endpoint}")

    return answer.summary_cents


def _answer_within(shopper_id: str,
                   look_up: LookUpSummary,
                   endpoint: CacheEndpoint,
                   patience_seconds: float) -> CacheAnswer:
    """The cache's reply, or `CacheTooSlow` once the budget is spent.

    Whatever the seam raises is raised on to the caller unchanged, exactly as
    it was when the call was made inline: a lookup that fails is still the
    caller's to see, and only the waiting is bounded here.

    A lookup that is abandoned is left to finish and its answer dropped. There
    is nothing to cancel on the other side of a blocking client, and a render
    that has already fallen back has no use for a figure that arrives late.
    """
    waiting = _WAITING_ON_THE_CACHE.submit(look_up, shopper_id)

    try:
        return waiting.result(timeout=patience_seconds)
    except concurrent.futures.TimeoutError:
        waiting.cancel()

        raise CacheTooSlow(
            f"{endpoint} did not answer within "
            f"{round(patience_seconds * _MILLISECONDS)}ms"
        ) from None


_MILLISECONDS = 1000
