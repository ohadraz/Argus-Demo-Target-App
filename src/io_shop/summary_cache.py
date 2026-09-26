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

And how long each one takes is the other half of the job. Falling back is free;
*dialling a cache that is not there* is not, because a refused or unanswered
connection costs the whole lookup timeout and the shop pays it once per render.
A cache pointed at the wrong port would otherwise put that timeout in front of
every account page in the shop, for as long as the configuration said so. So a
cache that refuses is remembered for a moment and not dialled again - see
`CacheCircuit`.

Where the cache lives is not this module's to know. The endpoint is deployment
configuration, handed in by whoever is running the shop, and how it is reached
is a seam the caller supplies.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


# How the cache is addressed, in the scheme its client speaks. Spelled out so
# that the endpoint in a failure line is the endpoint the shop actually dialled
# - a reader comparing it against what the configuration says is doing the one
# comparison that diagnoses this.
_ENDPOINT_FORMAT = "redis://{host}:{port}"

# How long the shop leaves a cache alone after it refused. Long enough that a
# misconfigured endpoint costs a handful of dials a minute rather than one per
# render, and short enough that a cache coming back is used again almost at
# once - the shop recovers on its own, without a deploy.
COOLDOWN_SECONDS = 30.0


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

    Raised both by a dial that failed and by a dial the shop declined to make
    because the last one failed. Both are the same fact for the page - the fast
    path is gone - and both name the endpoint, so the rate of these lines in the
    log stays the rate of affected requests either way.
    """


@dataclass
class CacheCircuit:
    """Which endpoints refused us lately, so the shop stops dialling them.

    A refused connection is cheap only in theory. In practice the shop finds
    out by waiting out its lookup timeout, and it finds out again on the next
    render, and the next - so a cache addressed at a port nothing listens on
    adds that timeout to every account page in the shop. The fallback keeps the
    pages correct and hides exactly how expensive that is.

    So a refusal is remembered. Inside the cooldown the endpoint is not dialled
    at all and the page goes straight to working the figure out; after it, the
    next request dials again, and a cache that answers is trusted immediately.
    Nothing is latched: this costs at most one timeout per cooldown and gives
    the cache back the moment it exists again.

    State, so it is an object rather than a function - and passed in where a
    caller wants one of its own, with a shared one for the ordinary case of a
    single shop process.
    """

    cooldown_seconds: float = COOLDOWN_SECONDS
    now: Callable[[], float] = time.monotonic
    _refused_at: dict[CacheEndpoint, float] = field(default_factory=dict)

    def is_open(self, endpoint: CacheEndpoint) -> bool:
        """Whether this endpoint is being left alone just now.

        Asking clears an expired cooldown, which is what makes the next request
        a probe rather than a permanent write-off.
        """
        refused_at = self._refused_at.get(endpoint)

        if refused_at is None:
            return False

        if self.now() - refused_at >= self.cooldown_seconds:
            del self._refused_at[endpoint]

            return False

        return True

    def seconds_left(self, endpoint: CacheEndpoint) -> float:
        """How long until this endpoint is dialled again, for the log line."""
        refused_at = self._refused_at.get(endpoint)

        if refused_at is None:
            return 0.0

        return max(0.0, self.cooldown_seconds - (self.now() - refused_at))

    def refused(self, endpoint: CacheEndpoint) -> None:
        """Records a dial that did not reach the cache."""
        self._refused_at[endpoint] = self.now()

    def reached(self, endpoint: CacheEndpoint) -> None:
        """Records a dial that did reach it - the cache is trusted again at
        once, because there is nothing left to protect the shop from."""
        self._refused_at.pop(endpoint, None)

    def forget(self) -> None:
        """Forgets every refusal. For a process starting over, and for tests."""
        self._refused_at.clear()


# The one a shop process shares, because a shop process has one cache and the
# point of remembering a refusal is that every render benefits from it.
SHARED_CACHE_CIRCUIT = CacheCircuit()


# How the cache is reached, given a shopper. A seam rather than a client, for
# the reason the payment provider's is one: the shop is rendered many times over
# to produce a minute of telemetry, and a connection per render would be
# thousands of them per read.
type LookUpSummary = Callable[[str], CacheAnswer]


def cached_summary(shopper_id: str,
                   look_up: LookUpSummary,
                   endpoint: CacheEndpoint,
                   circuit: CacheCircuit | None = None) -> int | None:
    """The figure the cache holds for this shopper, or `None` where it holds
    none.

    Raises `CacheUnreachable` when the cache did not answer, and the message is
    the point of the function: it names the endpoint the shop dialled, port
    included. A failure that said only "cache unavailable" would leave a reader
    knowing the cache is gone and unable to work out why - where the endpoint,
    set against the endpoint the configuration was supposed to carry, is the
    whole diagnosis.

    Raises it without dialling where this endpoint refused us within the
    cooldown. The caller cannot tell the two apart and should not: the page
    falls back either way, correct either way. The difference is that this one
    took no time at all, which is the difference between a cache outage being a
    slowdown and being a slowdown of every request in the shop.
    """
    circuit = SHARED_CACHE_CIRCUIT if circuit is None else circuit

    if circuit.is_open(endpoint):
        raise CacheUnreachable(
            f"not dialling {endpoint}: it refused the last connection, "
            f"trying again in {circuit.seconds_left(endpoint):.0f}s"
        )

    answer = look_up(shopper_id)

    if not answer.reached:
        circuit.refused(endpoint)

        raise CacheUnreachable(f"connection refused to {endpoint}")

    circuit.reached(endpoint)

    return answer.summary_cents
