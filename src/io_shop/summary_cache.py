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
The only thing that changes is how long each one takes - and that is the part
this module has to defend. A cache that is not there costs a connection attempt
per render, and a render happens on the busiest page the shop serves, so an
optional dependency that is dialled unconditionally is not optional at all: its
absence is paid for by every request in full. So the module remembers. A cache
that has refused several times running is left alone for a while, and the page
goes straight to working the figure out at the speed it would have had if no
cache had ever been configured.

Left alone rather than forgotten: every so often one request is allowed through
to find out whether the cache has come back, so recovery needs nobody to deploy
anything. And every request that would have used the cache is still told the
cache is gone, because a shop that stopped saying so would have hidden the only
signal this failure produces.

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

# How many refusals in a row mean the cache is gone rather than unlucky. Small,
# because the thing being protected is a per-request connection attempt on the
# shop's busiest page - and more than a couple of those is already the whole
# fast path's cost paid for nothing.
FAILURES_BEFORE_GIVING_UP: Final = 3

# How long the shop leaves a cache alone once it has given up on it. Long
# enough that a dead endpoint costs one connection attempt a minute rather than
# one per render; short enough that a cache which comes back is used again
# without anybody deploying anything.
HOW_LONG_TO_GIVE_UP_FOR_SECONDS: Final = 30.0


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

    Raised on a request the shop deliberately did not dial as well as on one it
    dialled and lost. The cache is just as gone either way, and a request that
    reported nothing because the shop had stopped asking would make the logs go
    quiet in the middle of the outage.
    """


# How the cache is reached, given a shopper. A seam rather than a client, for
# the reason the payment provider's is one: the shop is rendered many times over
# to produce a minute of telemetry, and a connection per render would be
# thousands of them per read.
type LookUpSummary = Callable[[str], CacheAnswer]

# What time it is, in seconds that only ever go forwards. A seam because the
# giving-up is a duration, and a duration nobody can move is a duration nobody
# can test.
type Now = Callable[[], float]


@dataclass
class _HowItHasBeenGoing:
    """What this endpoint has done lately: how many refusals in a row, and the
    moment before which the shop will not dial it again."""

    failures_in_a_row: int = 0
    do_not_dial_before: float = 0.0


# One record per endpoint, in the process, for the reason visits are kept in the
# process: the thing being avoided is a connection, so the memory has to live
# where the connections are made. Keyed by endpoint so that a deployment moving
# the cache starts with a clean record rather than inheriting the old address's.
_HOW_EACH_ENDPOINT_HAS_BEEN_GOING: dict[CacheEndpoint, _HowItHasBeenGoing] = {}


def cached_summary(shopper_id: str,
                   look_up: LookUpSummary,
                   endpoint: CacheEndpoint,
                   now: Now = time.monotonic) -> int | None:
    """The figure the cache holds for this shopper, or `None` where it holds
    none.

    Raises `CacheUnreachable` when the cache did not answer, and the message is
    the point of the function: it names the endpoint the shop dialled, port
    included. A failure that said only "cache unavailable" would leave a reader
    knowing the cache is gone and unable to work out why - where the endpoint,
    set against the endpoint the configuration was supposed to carry, is the
    whole diagnosis.

    Raises it without dialling at all where this endpoint has just refused
    several times running. That is the difference between losing the cache and
    paying for it: the page renders at the speed it would have had with no cache
    configured, instead of waiting on a socket nobody is listening to. The words
    say which of the two happened, and name the endpoint either way.
    """
    lately = _HOW_EACH_ENDPOINT_HAS_BEEN_GOING.setdefault(
        endpoint, _HowItHasBeenGoing()
    )

    if now() < lately.do_not_dial_before:
        raise CacheUnreachable(
            f"not dialled - {endpoint} refused "
            f"{lately.failures_in_a_row} connections in a row"
        )

    answer = look_up(shopper_id)

    if not answer.reached:
        lately.failures_in_a_row += 1

        if lately.failures_in_a_row >= FAILURES_BEFORE_GIVING_UP:
            lately.do_not_dial_before = now() + HOW_LONG_TO_GIVE_UP_FOR_SECONDS

        raise CacheUnreachable(f"connection refused to {endpoint}")

    # It answered, so whatever was wrong with it is over - including a run of
    # refusals that had not yet reached the point of giving up.
    lately.failures_in_a_row = 0
    lately.do_not_dial_before = 0.0

    return answer.summary_cents


def forget_every_cache_failure() -> None:
    """Drops what the shop remembers about every endpoint.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that has fixed the endpoint and does not want to wait out a
    cooldown, and for a test that wants the previous one's failures not to be
    its own.
    """
    _HOW_EACH_ENDPOINT_HAS_BEEN_GOING.clear()
</content>