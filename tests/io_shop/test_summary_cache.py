from __future__ import annotations

from collections.abc import Iterator

import pytest

from io_shop.summary_cache import (
    COOLDOWN_MS,
    CacheAnswer,
    CacheEndpoint,
    CacheUnreachable,
    LookUpSummary,
    cached_summary,
    dial_every_cache_again,
)

"""The cache the account page reads before it computes.

Three facts are worth pinning. A cache that answers with nothing and a cache
that cannot be reached are different things, even though both end in the page
working the figure out for itself; the second says which endpoint it failed at,
because that endpoint set against the configured one is the whole diagnosis
when a deployment moves the cache; and a cache that has just refused is left
alone for a while, because an optimisation that is dialled once per request is
only free while being refused is free.
"""

SOME_SHOPPER = "shopper-1"
SOME_ENDPOINT = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6379)


@pytest.fixture(autouse=True)
def _forget_remembered_refusals() -> Iterator[None]:
    """A fresh neighbourhood for every test.

    The refusal memory is process-wide, the way the visit book is, so a test
    that trips it would otherwise decide the next one's answer.
    """
    dial_every_cache_again()
    yield
    dial_every_cache_again()


class AClock:
    """Time the test moves by hand, in milliseconds."""

    def __init__(self, at: float = 0.0) -> None:
        self.now = at

    def __call__(self) -> float:
        return self.now

    def advance(self, ms: float) -> None:
        self.now += ms


class ACountedCache:
    """A cache that records how often the shop actually dialled it.

    Counting the dials rather than checking the figure is the point: a cache
    that is refused hands back the same answer however many times it is asked,
    so only the count can tell a shop that asks once from a shop that asks on
    every single request.
    """

    def __init__(self, *answers: CacheAnswer) -> None:
        self._answers = list(answers)
        self.dials = 0

    def __call__(self, dont_care_shopper: str) -> CacheAnswer:
        self.dials += 1

        if len(self._answers) > 1:
            return self._answers.pop(0)

        return self._answers[0]


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


def test_a_cache_that_refused_is_dialled_once_not_once_a_request() -> None:
    # The incident, in one assertion. Every one of these requests was refused
    # and every one of them fell back correctly - what made it an incident was
    # that each paid the cost of being refused. Whatever that costs, the shop
    # pays it once per cooldown.
    refusing = ACountedCache(CacheAnswer(reached=False))
    clock = AClock(at=1_000.0)

    for _ in range(200):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, refusing, SOME_ENDPOINT, now_ms=clock)

    assert refusing.dials == 1


def test_a_request_inside_the_cooldown_still_says_which_endpoint() -> None:
    # Not dialling must not cost the diagnosis. The address is still named, and
    # the words say the shop held off rather than tried and failed.
    refusing = ACountedCache(CacheAnswer(reached=False))
    clock = AClock(at=1_000.0)

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, refusing, SOME_ENDPOINT, now_ms=clock)

    with pytest.raises(CacheUnreachable) as held_off:
        cached_summary(SOME_SHOPPER, refusing, SOME_ENDPOINT, now_ms=clock)

    assert "6379" in str(held_off.value)
    assert "not dialled" in str(held_off.value)


def test_the_shop_dials_again_once_the_cooldown_is_over() -> None:
    # A cooldown that never ended would be the cache switched off for the life
    # of the process, which is a worse bargain than the one being fixed.
    refusing = ACountedCache(CacheAnswer(reached=False))
    clock = AClock(at=1_000.0)

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, refusing, SOME_ENDPOINT, now_ms=clock)

    clock.advance(COOLDOWN_MS)

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, refusing, SOME_ENDPOINT, now_ms=clock)

    assert refusing.dials == 2


def test_a_cache_that_comes_back_is_used_every_request_again() -> None:
    # The refusal is forgotten the moment the cache answers, so a cache that
    # recovers is a fast path again rather than one dialled every five seconds.
    recovering = ACountedCache(
        CacheAnswer(reached=False),
        CacheAnswer(reached=True, summary_cents=999),
    )
    clock = AClock(at=1_000.0)

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, recovering, SOME_ENDPOINT, now_ms=clock)

    clock.advance(COOLDOWN_MS)

    assert cached_summary(
        SOME_SHOPPER, recovering, SOME_ENDPOINT, now_ms=clock
    ) == 999
    assert cached_summary(
        SOME_SHOPPER, recovering, SOME_ENDPOINT, now_ms=clock
    ) == 999
    assert recovering.dials == 3
