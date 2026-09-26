from __future__ import annotations

import pytest

from io_shop.summary_cache import (
    CacheAnswer,
    CacheCircuit,
    CacheEndpoint,
    CacheUnreachable,
    LookUpSummary,
    cached_summary,
)

"""The cache the account page reads before it computes.

Three facts are worth pinning. A cache that answers with nothing and a cache
that cannot be reached are different things, even though both end in the page
working the figure out for itself - and the second says which endpoint it
failed at, because that endpoint set against the configured one is the whole
diagnosis when a deployment moves the cache.

The third is what that diagnosis cost. Finding out that a cache is not there
means waiting out a lookup timeout, so a shop that finds out once per render
puts that timeout in front of every account page it serves. It finds out once
per cooldown instead.
"""

SOME_SHOPPER = "shopper-1"
SOME_ENDPOINT = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6379)


def a_cache_holding(summary_cents: int) -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(
        reached=True, summary_cents=summary_cents
    )


def a_cache_holding_nothing() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=True)


def a_cache_that_cannot_be_reached() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=False)


def test_a_cache_that_holds_the_figure_answers_with_it() -> None:
    found = cached_summary(SOME_SHOPPER, a_cache_holding(1234), SOME_ENDPOINT)

    assert found == 1234


def test_a_cache_that_holds_nothing_answers_with_nothing() -> None:
    # An ordinary miss. Every entry has a first request, and nothing about this
    # is worth reporting.
    found = cached_summary(SOME_SHOPPER, a_cache_holding_nothing(), SOME_ENDPOINT)

    assert found is None


def test_a_cache_that_cannot_be_reached_is_not_a_miss() -> None:
    # The distinction the module exists for. A miss is one shopper's first
    # visit; this is the shop's fast path gone for everybody at once, and a
    # module answering `None` to both would have no way to say which.
    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, a_cache_that_cannot_be_reached(), SOME_ENDPOINT)


def test_an_unreachable_cache_names_the_endpoint_it_failed_at() -> None:
    # The port is the diagnosis. A reader who has this line and the values file
    # has everything they need, and one who has only "cache unavailable" has to
    # go and find out what the shop was even dialling.
    with pytest.raises(CacheUnreachable) as unreachable:
        cached_summary(SOME_SHOPPER, a_cache_that_cannot_be_reached(), SOME_ENDPOINT)

    assert "cache.io-shop.svc.cluster.local" in str(unreachable.value)
    assert "6379" in str(unreachable.value)


def test_an_endpoint_reads_as_the_address_its_client_would_dial() -> None:
    assert str(SOME_ENDPOINT) == "redis://cache.io-shop.svc.cluster.local:6379"


class ACountedCache:
    """A cache that records how often the shop dialled it.

    The count is the point. Whether the page renders correctly says nothing
    about this incident - it rendered correctly throughout - and the only thing
    that changed when the port moved was how many times an hour of traffic sat
    waiting out a lookup timeout.
    """

    def __init__(self, *, reachable: bool, summary_cents: int | None = None) -> None:
        self.reachable = reachable
        self.summary_cents = summary_cents
        self.dials = 0

    def __call__(self, dont_care_shopper: str) -> CacheAnswer:
        self.dials += 1

        return CacheAnswer(
            reached=self.reachable,
            summary_cents=self.summary_cents if self.reachable else None,
        )


class AClockWeControl:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def tick(self, seconds: float) -> None:
        self.seconds += seconds


def a_circuit_on(clock: AClockWeControl, cooldown: float = 30.0) -> CacheCircuit:
    return CacheCircuit(cooldown_seconds=cooldown, now=clock)


def test_a_refused_cache_is_not_dialled_again_straight_away() -> None:
    # The incident, in one assertion. Two hundred renders against a cache that
    # is not there used to be two hundred lookup timeouts - which is what took
    # p50 from 26ms to 180ms while every page stayed perfectly correct. One
    # dial, then the shop leaves it alone.
    clock = AClockWeControl()
    circuit = a_circuit_on(clock)
    cache = ACountedCache(reachable=False)

    for _ in range(50):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, circuit)

    assert cache.dials == 1


def test_a_dial_the_shop_declined_still_names_the_endpoint() -> None:
    # Not dialling must not mean not saying so. The rate of these lines stays
    # the rate of affected requests, so the log a reader diagnoses from is the
    # same log - it is only the waiting that goes away.
    clock = AClockWeControl()
    circuit = a_circuit_on(clock)
    cache = ACountedCache(reachable=False)

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, circuit)

    with pytest.raises(CacheUnreachable) as declined:
        cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, circuit)

    assert "cache.io-shop.svc.cluster.local" in str(declined.value)
    assert "6379" in str(declined.value)


def test_the_shop_tries_the_cache_again_once_the_cooldown_is_up() -> None:
    # Nothing is latched. A cache written off for good would mean a port put
    # back needed a deploy to be noticed.
    clock = AClockWeControl()
    circuit = a_circuit_on(clock, cooldown=30.0)
    cache = ACountedCache(reachable=False)

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, circuit)

    clock.tick(31.0)

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, circuit)

    assert cache.dials == 2


def test_a_cache_that_comes_back_is_used_again_immediately() -> None:
    # The recovery this rests on: the shop is fast again a cooldown after the
    # cache exists, without anybody redeploying it.
    clock = AClockWeControl()
    circuit = a_circuit_on(clock, cooldown=30.0)

    with pytest.raises(CacheUnreachable):
        cached_summary(
            SOME_SHOPPER, ACountedCache(reachable=False), SOME_ENDPOINT, circuit
        )

    clock.tick(31.0)
    recovered = ACountedCache(reachable=True, summary_cents=4321)

    assert cached_summary(SOME_SHOPPER, recovered, SOME_ENDPOINT, circuit) == 4321
    assert cached_summary(SOME_SHOPPER, recovered, SOME_ENDPOINT, circuit) == 4321
    assert recovered.dials == 2


def test_a_healthy_cache_is_dialled_every_time() -> None:
    # The circuit must be invisible when nothing is wrong: a shop that skipped
    # dials it could have made would be a shop with a worse hit ratio than the
    # one it is protecting.
    clock = AClockWeControl()
    circuit = a_circuit_on(clock)
    cache = ACountedCache(reachable=True, summary_cents=77)

    for _ in range(5):
        assert cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, circuit) == 77

    assert cache.dials == 5


def test_one_endpoint_refusing_says_nothing_about_another() -> None:
    clock = AClockWeControl()
    circuit = a_circuit_on(clock)
    elsewhere = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6380)
    healthy = ACountedCache(reachable=True, summary_cents=5)

    with pytest.raises(CacheUnreachable):
        cached_summary(
            SOME_SHOPPER, ACountedCache(reachable=False), elsewhere, circuit
        )

    assert cached_summary(SOME_SHOPPER, healthy, SOME_ENDPOINT, circuit) == 5
