from __future__ import annotations

import pytest

from io_shop.summary_cache import (
    CacheAnswer,
    CacheEndpoint,
    CacheUnreachable,
    LookUpSummary,
    SummaryEntry,
    cache_key_for,
    cached_summary,
    summary_entry_in,
)

"""The cache the account page reads before it computes.

Four facts are worth pinning. A cache that answers with nothing and a cache
that cannot be reached are different things, even though both end in the page
working the figure out for itself - and the second says which endpoint it
failed at, because that endpoint set against the configured one is the whole
diagnosis when a deployment moves the cache.

The third is the shape. An entry holds a figure and the number of purchases it
covers, written down together, and this revision reads that shape and no other
- text carrying anything else is text it passes over, which the page answers
the way it answers a shopper the cache has never seen.

The fourth is where that shape is kept. A cache is shared by every replica at
once, including replicas of the revision before this one during a rolling
update, so entries of this shape live under keys that name it - and a revision
speaking another shape never finds one.
"""

SOME_SHOPPER = "shopper-1"
SOME_ENDPOINT = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6379)


def an_entry(amount_cents: int = 1234, items_counted: int = 8) -> SummaryEntry:
    return SummaryEntry(amount_cents=amount_cents, items_counted=items_counted)


def a_cache_holding(written: str) -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=True, entry=written)


def a_cache_holding_nothing() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=True)


def a_cache_that_cannot_be_reached() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=False)


def test_a_cache_that_holds_the_figure_answers_with_it() -> None:
    found = cached_summary(
        SOME_SHOPPER, a_cache_holding(str(an_entry())), SOME_ENDPOINT
    )

    assert found == an_entry()


def test_an_entry_carries_what_the_figure_covers() -> None:
    # Both fields, because the page shows both - what a shopper has spent and
    # over how many purchases. An entry holding the first alone would send the
    # page back to the history for the second, which is the walk the cache
    # exists to save.
    found = cached_summary(
        SOME_SHOPPER,
        a_cache_holding(str(an_entry(amount_cents=4500, items_counted=3))),
        SOME_ENDPOINT
    )

    assert found is not None
    assert found.amount_cents == 4500
    assert found.items_counted == 3


def test_an_entry_is_written_down_as_the_figure_and_its_count() -> None:
    # The writer, and the one spelling of an entry there is. Whatever puts an
    # entry in writes it this way, and whatever quotes an unreadable one quotes
    # this text.
    assert str(an_entry(amount_cents=4500, items_counted=3)) == "4500/3"


def test_an_entry_is_looked_for_under_a_key_that_names_its_shape() -> None:
    # The compatibility break this exists to prevent. A cache is shared by every
    # replica, and a rolling update - or one paused half way - has the revision
    # before this one live against it, reading an entry as a bare figure and
    # failing on anything else. So an entry of this shape is kept somewhere that
    # revision does not look: it is not the bare shopper id, and the shape's
    # version is in it. Neither revision meets the other's writing, and a shape
    # a revision cannot read costs it a miss rather than a failed page.
    asked_for: list[str] = []

    def a_cache_recording_what_it_was_asked_for(key: str) -> CacheAnswer:
        asked_for.append(key)
        return CacheAnswer(reached=True)

    cached_summary(
        SOME_SHOPPER, a_cache_recording_what_it_was_asked_for, SOME_ENDPOINT
    )

    assert asked_for == [cache_key_for(SOME_SHOPPER)]
    assert asked_for[0] != SOME_SHOPPER
    assert SOME_SHOPPER in asked_for[0]


def test_a_key_keeps_one_shape_apart_from_another() -> None:
    # Two shoppers still have two keys - the version separates shapes, not
    # shoppers - and the key is stable, because a writer and a reader compose it
    # with the same function.
    assert cache_key_for("shopper-1") != cache_key_for("shopper-2")
    assert cache_key_for(SOME_SHOPPER) == cache_key_for(SOME_SHOPPER)


def test_text_that_is_not_an_entry_holds_no_entry() -> None:
    # This revision reads the shape above and no other. Text carrying a bare
    # figure - the shape stored before an entry had a count - is not an entry
    # here, and there is no read path that makes it one.
    assert summary_entry_in("4500") is None


def test_text_whose_fields_are_not_numbers_holds_no_entry() -> None:
    assert summary_entry_in("four thousand/three") is None


def test_a_cache_holding_something_unreadable_is_answered_as_a_miss() -> None:
    # Not an exception. This module fails no page: an entry that cannot be read
    # is, to the page in front of it, an entry that is not there, and the page
    # works the figure out for itself exactly as it does on a first visit.
    found = cached_summary(SOME_SHOPPER, a_cache_holding("4500"), SOME_ENDPOINT)

    assert found is None


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
