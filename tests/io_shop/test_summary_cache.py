from __future__ import annotations

import pytest

from io_shop.summary_cache import (
    FAILURES_BEFORE_PAUSING,
    PAUSE_SECONDS,
    CacheAnswer,
    CacheEndpoint,
    CacheUnreachable,
    LookUpSummary,
    SummaryEntry,
    cached_summary,
    forget_cache_failures,
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

The fourth is what losing it costs. The fallback keeps every page correct, so
an unreachable cache is a slowdown and not an outage - but only if not reaching
it is cheap. A shop that re-dialled a refusing endpoint on each of 1200
requests a minute would pay the connect-and-fail 1200 times a minute, and the
correctness of every page would hide that in the latency rather than the error
rate. So the shop stops dialling, keeps saying so, and tries again shortly.
"""

SOME_SHOPPER = "shopper-1"
SOME_ENDPOINT = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6379)


@pytest.fixture(autouse=True)
def a_shop_that_has_just_started() -> None:
    """Every case begins knowing nothing about any endpoint.

    What a new process starts with. Without this each test would inherit the
    refusals of the last - module state leaking into the tests about module
    state, which is a poor thing to have in this file of all files.
    """
    forget_cache_failures()


def an_entry(amount_cents: int = 1234, items_counted: int = 8) -> SummaryEntry:
    return SummaryEntry(amount_cents=amount_cents, items_counted=items_counted)


def a_cache_holding(written: str) -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=True, entry=written)


def a_cache_holding_nothing() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=True)


def a_cache_that_cannot_be_reached() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=False)


class CountingDials:
    """A cache that records how often the shop actually dialled it.

    The count is what the last few cases in this file are about: what went
    wrong in production was not the answers the shop got but the number of
    times it went and asked for them.
    """

    def __init__(self, reachable: bool = False) -> None:
        self.reachable = reachable
        self.dials = 0

    def __call__(self, dont_care_shopper: str) -> CacheAnswer:
        self.dials += 1

        if not self.reachable:
            return CacheAnswer(reached=False)

        return CacheAnswer(reached=True, entry=str(an_entry()))


class AFrozenClock:
    """A clock that moves only when a test moves it."""

    def __init__(self) -> None:
        self.seconds = 1000.0

    def __call__(self) -> float:
        return self.seconds

    def move_on(self, seconds: float) -> None:
        self.seconds += seconds


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


def test_one_refusal_does_not_stop_the_shop_dialling() -> None:
    # A single refused connection is the sort of thing a busy cache does. A
    # shop that gave up on the first would lose its fast path to noise.
    cache = CountingDials()
    clock = AFrozenClock()

    for _ in range(2):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    assert cache.dials == 2


def test_a_cache_that_keeps_refusing_is_not_dialled_on_every_request() -> None:
    # The incident, in one assertion. Every one of these requests is correct
    # either way - the page falls back and renders - so what went wrong was
    # never the answer, it was that the shop paid a connect-and-fail for each
    # of them. Once an endpoint has refused enough times in a row, the shop
    # stops asking for a while.
    cache = CountingDials()
    clock = AFrozenClock()

    for _ in range(200):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    assert cache.dials == FAILURES_BEFORE_PAUSING


def test_a_paused_cache_still_reports_the_endpoint_it_cannot_reach() -> None:
    # Not dialling must not cost the diagnosis. Every request still hears that
    # it had no cache, and still hears which endpoint the shop would have
    # dialled - a module that went quiet once it stopped checking would make
    # the fast path look as though it had come back.
    cache = CountingDials()
    clock = AFrozenClock()

    for _ in range(FAILURES_BEFORE_PAUSING):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    with pytest.raises(CacheUnreachable) as unreachable:
        cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    assert "cache.io-shop.svc.cluster.local" in str(unreachable.value)
    assert "6379" in str(unreachable.value)


def test_the_shop_tries_again_once_the_pause_is_over() -> None:
    # A pause, not a decision. One request per window goes and finds out.
    cache = CountingDials()
    clock = AFrozenClock()

    for _ in range(FAILURES_BEFORE_PAUSING + 10):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    clock.move_on(PAUSE_SECONDS + 1)

    with pytest.raises(CacheUnreachable):
        cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    assert cache.dials == FAILURES_BEFORE_PAUSING + 1


def test_a_cache_that_comes_back_is_used_again() -> None:
    # Recovery without a deploy and without a restart: the port goes back to
    # what it was, the next probe gets an answer, and the fast path returns.
    cache = CountingDials()
    clock = AFrozenClock()

    for _ in range(FAILURES_BEFORE_PAUSING + 10):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock)

    cache.reachable = True
    clock.move_on(PAUSE_SECONDS + 1)

    assert cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock) == an_entry()

    # And is dialled normally from then on, rather than staying half-trusted.
    assert cached_summary(SOME_SHOPPER, cache, SOME_ENDPOINT, now=clock) == an_entry()
    assert cache.dials == FAILURES_BEFORE_PAUSING + 2


def test_one_endpoint_refusing_does_not_pause_another() -> None:
    # What the shop learned is about an address, not about caching in general.
    broken = CountingDials()
    working = CountingDials(reachable=True)
    another_endpoint = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6380)
    clock = AFrozenClock()

    for _ in range(FAILURES_BEFORE_PAUSING + 5):
        with pytest.raises(CacheUnreachable):
            cached_summary(SOME_SHOPPER, broken, another_endpoint, now=clock)

    assert cached_summary(SOME_SHOPPER, working, SOME_ENDPOINT, now=clock) == an_entry()
