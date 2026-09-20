from __future__ import annotations

import pytest

from io_shop.summary_cache import (
    CacheAnswer,
    CacheEndpoint,
    CacheUnreachable,
    LookUpSummary,
    cached_summary,
)

"""The cache the account page reads before it computes.

Two facts are worth pinning. A cache that answers with nothing and a cache that
cannot be reached are different things, even though both end in the page
working the figure out for itself - and the second says which endpoint it
failed at, because that endpoint set against the configured one is the whole
diagnosis when a deployment moves the cache.
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
