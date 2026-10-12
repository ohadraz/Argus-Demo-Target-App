from __future__ import annotations

import time
from threading import Event

import pytest

from io_shop.pricing_service import (
    PRICING_DEADLINE_MS,
    PRICING_HOST,
    SLOW_CALL_MS,
    AskThePricingService,
    PricingAnswer,
    PricingServiceFailed,
    basket_total,
)

"""What the shop asks another team's service, and what it says about the asking.

Three things worth pinning, and the last is what the incident rests on. A
service that answers no price fails the page, in words that name the service
rather than the shop. A service that answers slowly does not fail anything - it
produces a line saying where the time went, which is the only evidence a caller
has about a dependency nobody is watching. And a service that does not answer at
all does not get to decide how long an account page takes: the shop waits its
own deadline and leaves.
"""


def a_service_answering_in(took_ms: int) -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(
        total_cents=8400, took_ms=took_ms
    )


def a_service_with_no_price() -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(total_cents=None, took_ms=6)


def a_service_that_stalls(released: Event) -> AskThePricingService:
    """A service that answers only once the test lets it.

    Stalling on an event rather than sleeping a fixed time, so the test never
    waits longer than it must and the abandoned call is released before the
    test ends rather than left running.
    """
    def ask(dont_care_shopper: str) -> PricingAnswer:
        released.wait(timeout=10)

        return PricingAnswer(total_cents=8400, took_ms=1500)

    return ask


def test_a_prompt_answer_is_a_price_and_nothing_to_report() -> None:
    priced = basket_total("some-shopper", a_service_answering_in(12))

    assert priced.total_cents == 8400
    assert priced.slow_call is None


def test_an_answer_at_the_threshold_is_already_worth_reporting() -> None:
    """The boundary belongs to the slow side.

    A threshold nobody can state exactly is a threshold that drifts, and the
    first value a reader would call slow is the value itself.
    """
    priced = basket_total("some-shopper", a_service_answering_in(SLOW_CALL_MS))

    assert priced.slow_call is not None


def test_a_slow_answer_is_still_a_price() -> None:
    priced = basket_total("some-shopper", a_service_answering_in(1500))

    assert priced.total_cents == 8400


def test_a_slow_call_names_the_service_the_path_and_the_milliseconds() -> None:
    priced = basket_total("shopper-42", a_service_answering_in(1500))

    assert priced.slow_call is not None
    assert PRICING_HOST in priced.slow_call
    assert "1500ms" in priced.slow_call
    assert "shopper-42" in priced.slow_call


def test_no_price_fails_in_words_that_name_the_service() -> None:
    with pytest.raises(PricingServiceFailed) as raised:
        basket_total("shopper-42", a_service_with_no_price())

    assert PRICING_HOST in str(raised.value)


def test_a_service_that_stalls_does_not_hold_the_shop_past_the_deadline() -> None:
    """The incident, in one assertion.

    The upstream took 1500ms per call and the shop's own p50 went with it,
    one for one, because the shop waited as long as it took. What the shop
    spends on the basket is now its own to decide: the call is abandoned at the
    deadline whatever the service is doing.
    """
    released = Event()
    deadline_ms = 50

    try:
        started = time.monotonic()
        priced = basket_total(
            "shopper-42", a_service_that_stalls(released), deadline_ms=deadline_ms
        )
        waited_ms = (time.monotonic() - started) * 1000
    finally:
        released.set()

    assert waited_ms < deadline_ms * 5
    assert priced.total_cents is None


def test_an_abandoned_call_says_what_was_dropped_and_after_how_long() -> None:
    released = Event()

    try:
        priced = basket_total(
            "shopper-42", a_service_that_stalls(released), deadline_ms=50
        )
    finally:
        released.set()

    assert priced.slow_call is not None
    assert PRICING_HOST in priced.slow_call
    assert "50ms" in priced.slow_call
    assert "shopper-42" in priced.slow_call


def test_an_answer_inside_the_deadline_is_untouched_by_it() -> None:
    """The deadline is a cap on waiting, not a second reporting threshold.

    A call slower than the shop remarks on but quicker than it gives up on is
    still a price, and still reports the milliseconds it actually took.
    """
    took_ms = PRICING_DEADLINE_MS - 1
    priced = basket_total("shopper-42", a_service_answering_in(took_ms))

    assert priced.total_cents == 8400
    assert priced.slow_call is not None
    assert f"{took_ms}ms" in priced.slow_call
