from __future__ import annotations

import time

import pytest

from io_shop.summary_cache import (
    CacheAnswer,
    CacheEndpoint,
    CacheTooSlow,
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

The third is the one this module now guarantees: a cache that answers too
slowly is given up on. It is the same fallback and it is reached in time,
because a page that waits without limit on an optimisation has made the
optimisation's latency its own.
"""

SOME_SHOPPER = "shopper-1"
SOME_ENDPOINT = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6379)

# Long enough that a test which waited it out would be unmistakable, and short
# enough that the abandoned lookup has finished before the suite ends.
LONGER_THAN_ANY_BUDGET_HERE = 1.0
A_SHORT_BUDGET = 0.05


def a_cache_holding(summary_cents: int) -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(
        reached=True, summary_cents=summary_cents
    )


def a_cache_holding_nothing() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=True)


def a_cache_that_cannot_be_reached() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=False)


def a_cache_that_takes(seconds: float) -> LookUpSummary:
    """A cache that answers perfectly well, eventually.

    The shape of the incident: nothing refuses, nothing errors, every answer is
    correct and every one of them arrives far too late to be worth having.
    """
    def look_up(dont_care_shopper: str) -> CacheAnswer:
        time.sleep(seconds)

        return CacheAnswer(reached=True, summary_cents=1234)

    return look_up


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


def test_a_cache_that_will_not_answer_in_time_is_given_up_on() -> None:
    # The incident. Waiting for this answer costs the upstream's latency on
    # every render, and the figure it would have saved can be worked out here.
    started = time.monotonic()

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER,
                       a_cache_that_takes(LONGER_THAN_ANY_BUDGET_HERE),
                       SOME_ENDPOINT,
                       patience_seconds=A_SHORT_BUDGET)

    assert time.monotonic() - started < LONGER_THAN_ANY_BUDGET_HERE / 2


def test_giving_up_names_the_endpoint_and_the_wait() -> None:
    # A slow cache and a refused one have the same effect on a render and
    # completely different causes, so the words have to tell them apart.
    with pytest.raises(CacheTooSlow) as too_slow:
        cached_summary(SOME_SHOPPER,
                       a_cache_that_takes(LONGER_THAN_ANY_BUDGET_HERE),
                       SOME_ENDPOINT,
                       patience_seconds=A_SHORT_BUDGET)

    assert "cache.io-shop.svc.cluster.local" in str(too_slow.value)
    assert "50ms" in str(too_slow.value)


def test_a_slow_cache_is_still_an_unreachable_one_to_the_page() -> None:
    # The page catches `CacheUnreachable` and falls back. A timeout that was
    # not one of those would escape the boundary's cache handling and become a
    # failed request instead of a slower correct one.
    assert issubclass(CacheTooSlow, CacheUnreachable)


def test_an_endpoint_reads_as_the_address_its_client_would_dial() -> None:
    assert str(SOME_ENDPOINT) == "redis://cache.io-shop.svc.cluster.local:6379"
