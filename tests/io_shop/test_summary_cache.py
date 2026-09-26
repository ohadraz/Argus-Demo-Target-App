from __future__ import annotations

import pytest

from io_shop.summary_cache import (
    FAILURES_BEFORE_GIVING_UP,
    HOW_LONG_TO_GIVE_UP_FOR_SECONDS,
    CacheAnswer,
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

And a cache that keeps refusing stops being dialled. That is what keeps an
optional dependency optional: the page is correct either way, but a shop that
dialled a dead endpoint once per render would pay a connection timeout on its
busiest page for as long as the endpoint stayed wrong.
"""

SOME_SHOPPER = "shopper-1"
SOME_ENDPOINT = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6379)


class AClock:
    """Time that only moves when a test moves it - because the giving-up is a
    duration, and a test that waited one out would be a slow test."""

    def __init__(self) -> None:
        self.seconds = 1000.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, by_seconds: float) -> None:
        self.seconds += by_seconds


class ACountedCache:
    """A cache that remembers how many times it was actually dialled.

    The count is the assertion this file's newest tests are about: what a dead
    cache costs the shop is connection attempts, so the only way to say the
    cost is bounded is to count them.
    """

    def __init__(self, *answers: CacheAnswer, then: CacheAnswer) -> None:
        self.answers = list(answers)
        self.then = then
        self.dials = 0

    def __call__(self, dont_care_shopper: str) -> CacheAnswer:
        self.dials += 1

        if self.answers:
            return self.answers.pop(0)

        return self.then


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


def test_a_cache_that_keeps_refusing_stops_being_dialled() -> None:
    # The incident, in one assertion. A deployment pointed the shop at a port
    # nothing was listening on; the pages stayed correct and every one of them
    # paid a connection attempt, which is how an optional cache became the
    # shop's latency. The attempts have to stop even though the requests do not.
    clock = AClock()
    cache = ACountedCache(then=CacheAnswer(reached=False))

    for _ in range(FAILURES_BEFORE_GIVING_UP + 20):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    assert cache.dials == FAILURES_BEFORE_GIVING_UP


def test_a_request_that_was_not_dialled_still_reports_the_cache_as_gone() -> None:
    # Giving up on the cache must not mean going quiet about it. The endpoint is
    # still named, because it is still the diagnosis.
    clock = AClock()
    cache = ACountedCache(then=CacheAnswer(reached=False))

    for _ in range(FAILURES_BEFORE_GIVING_UP):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    with pytest.raises(CacheUnreachable) as unreachable:
        cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    assert "6379" in str(unreachable.value)


def test_the_shop_tries_again_once_it_has_waited() -> None:
    # Left alone, not forgotten: a cache that comes back is used again without
    # anybody deploying anything.
    clock = AClock()
    refusals = [CacheAnswer(reached=False)] * FAILURES_BEFORE_GIVING_UP
    cache = ACountedCache(
        *refusals, then=CacheAnswer(reached=True, summary_cents=777)
    )

    for _ in range(FAILURES_BEFORE_GIVING_UP):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    clock.advance(HOW_LONG_TO_GIVE_UP_FOR_SECONDS + 1)

    assert cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock) == 777


def test_a_cache_that_answers_again_is_dialled_every_time() -> None:
    # And the giving-up does not linger once it is over: a recovered cache is
    # back to being the fast path, not one probe a minute.
    clock = AClock()
    refusals = [CacheAnswer(reached=False)] * FAILURES_BEFORE_GIVING_UP
    cache = ACountedCache(
        *refusals, then=CacheAnswer(reached=True, summary_cents=777)
    )

    for _ in range(FAILURES_BEFORE_GIVING_UP):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    clock.advance(HOW_LONG_TO_GIVE_UP_FOR_SECONDS + 1)

    for _ in range(5):
        assert cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock) == 777

    assert cache.dials == FAILURES_BEFORE_GIVING_UP + 5


def test_the_shop_does_not_give_up_on_an_endpoint_it_reached_in_between() -> None:
    # A refusal here and there is not a cache that is gone. Only a run of them
    # is, so an answer in the middle has to clear what came before it.
    clock = AClock()
    a_wobble = [CacheAnswer(reached=False)] * (FAILURES_BEFORE_GIVING_UP - 1)
    cache = ACountedCache(
        *a_wobble,
        CacheAnswer(reached=True, summary_cents=42),
        *a_wobble,
        then=CacheAnswer(reached=True, summary_cents=42),
    )

    for _ in range(len(a_wobble)):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    assert cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock) == 42

    for _ in range(len(a_wobble)):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    assert cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock) == 42


def test_giving_up_on_one_endpoint_is_not_giving_up_on_another() -> None:
    # The memory is about an address, not about caching in general - so a
    # deployment that moves the cache starts clean rather than inheriting the
    # old address's failures.
    clock = AClock()
    somewhere_else = CacheEndpoint(host=SOME_ENDPOINT.host, port=6380)
    working = ACountedCache(then=CacheAnswer(reached=True, summary_cents=5))

    for _ in range(FAILURES_BEFORE_GIVING_UP + 2):
        with pytest.raises(CacheUnreachable):
            cached_summary(
                SOME_SHOPPER, a_cache_that_cannot_be_reached(), somewhere_else,
                now=clock
            )

    assert cached_summary(SOME_SHOPPER, working, SOME_ENDPOINT, now=clock) == 5
</content>