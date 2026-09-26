"""The spend figure Io has already worked out once, kept so it need not again.

Working the figure out means walking a shopper's whole purchase history, and an
account page is the most-visited page the shop has. So the figure is cached
under the shopper it belongs to, and the page reads the cache before it
computes - which is what makes the shop fast rather than merely correct.

Nothing here fails a page. A cache that has nothing for this shopper, and a
cache that cannot be reached at all, both end the same way: the page works the
figure out for itself and renders. That is the designed behaviour and it is
load-bearing - a shop that failed when its cache did would be a shop whose cache
is a dependency rather than an optimisation, and losing it would be an outage
instead of a slowdown.

Which is also why losing it is so easy to miss. Every page is still correct.
The only thing that changes is how long each one takes.

And that is the thing this module has to protect. An optimisation is only an
optimisation while asking it is cheap; a cache that refuses slowly costs every
render what it costs to be refused, and a shop that dialled it once per request
would turn a dead cache into a shop-wide slowdown. So a refusal is remembered:
for a short while afterwards the shop does not dial that endpoint at all, and
pays nothing to find out what it already knows.

Where the cache lives is not this module's to know. The endpoint is deployment
configuration, handed in by whoever is running the shop, and how it is reached
is a seam the caller supplies.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final


# How the cache is addressed, in the scheme its client speaks. Spelled out so
# that the endpoint in a failure line is the endpoint the shop actually dialled
# - a reader comparing it against what the configuration says is doing the one
# comparison that diagnoses this.
_ENDPOINT_FORMAT = "redis://{host}:{port}"

# How long the shop leaves a refusing endpoint alone before trying it again.
# Short enough that a cache coming back is used within seconds, long enough that
# a cache that is gone is dialled once in a while rather than once a request.
# The number is a bound on damage, not a guess at a repair time: whatever a
# refusal costs - a fast reset or a blocking wait against a closed port - the
# shop pays it once per window instead of once per render.
COOLDOWN_MS: Final = 5_000.0


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


# How the cache is reached, given a shopper. A seam rather than a client, for
# the reason the payment provider's is one: the shop is rendered many times over
# to produce a minute of telemetry, and a connection per render would be
# thousands of them per read.
type LookUpSummary = Callable[[str], CacheAnswer]

# What time it is, in milliseconds, for deciding whether a refusal is still
# fresh. A seam so the cooldown can be tested without waiting out, and because
# nothing else here reads a clock.
type Clock = Callable[[], float]


# When each endpoint may be dialled again, by address. Process-wide, the way the
# visit book is: the refusal is a fact about the shop's neighbourhood rather
# than about one request, and a per-request memory would remember nothing.
_NOT_BEFORE: dict[str, float] = {}


def _monotonic_ms() -> float:
    """Now, in milliseconds, off a clock that cannot go backwards."""
    return time.monotonic() * 1000.0


def dial_every_cache_again() -> None:
    """Forgets every remembered refusal.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that has just moved its cache and does not want to wait out a
    cooldown to find out it worked.
    """
    _NOT_BEFORE.clear()


def cached_summary(shopper_id: str,
                   look_up: LookUpSummary,
                   endpoint: CacheEndpoint,
                   now_ms: Clock | None = None) -> int | None:
    """The figure the cache holds for this shopper, or `None` where it holds
    none.

    Raises `CacheUnreachable` when the cache did not answer, and the message is
    the point of the function: it names the endpoint the shop dialled, port
    included. A failure that said only "cache unavailable" would leave a reader
    knowing the cache is gone and unable to work out why - where the endpoint,
    set against the endpoint the configuration was supposed to carry, is the
    whole diagnosis.

    An endpoint that refused recently is not dialled at all: the same exception
    is raised straight away, in words that say the shop did not dial. That is
    what keeps a dead cache a slowdown of one request per cooldown rather than
    of every request there is, however long its client takes to give up.
    """
    clock = now_ms if now_ms is not None else _monotonic_ms
    address = str(endpoint)
    not_before = _NOT_BEFORE.get(address)
    now = clock()

    if not_before is not None and now < not_before:
        raise CacheUnreachable(
            f"connection refused to {endpoint} - not dialled, "
            f"{round(not_before - now)}ms left of the {round(COOLDOWN_MS)}ms "
            "the shop leaves a refusing cache alone"
        )

    answer = look_up(shopper_id)

    if not answer.reached:
        _NOT_BEFORE[address] = clock() + COOLDOWN_MS

        raise CacheUnreachable(f"connection refused to {endpoint}")

    # It answered, so whatever was wrong is over: back to dialling every
    # request, which is the arrangement that makes the cache worth having.
    _NOT_BEFORE.pop(address, None)

    return answer.summary_cents
