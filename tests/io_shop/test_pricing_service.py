from __future__ import annotations

import threading
import time

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

Two things worth pinning, and the second is the one the incident rests on. A
service that answers no price fails the page, in words that name the service
rather than the shop. And a service that answers slowly does not fail anything -
it produces a line saying where the time went, which is the only evidence a
caller has about a dependency nobody is watching.
"""


def a_service_answering_in(took_ms: int) -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(
        total_cents=8400, took_ms=took_ms
    )


def a_service_with_no_price() -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(total_cents=None, took_ms=6)


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


def test_a_service_that_does_not_answer_in_time_does_not_hold_the_page() -> None:
    """The incident: io-pricing took 1500ms and the page waited all of it.

    A call past the deadline is given up on as no price, so the account page
    renders without its basket total instead of inheriting pricing's latency.
    """
    release = threading.Event()

    def a_service_that_hangs(dont_care_shopper: str) -> PricingAnswer:
        release.wait(10)
        return PricingAnswer(total_cents=8400, took_ms=10_000)

    started = time.monotonic()
    try:
        with pytest.raises(PricingServiceFailed) as raised:
            basket_total("shopper-42", a_service_that_hangs)
        waited_ms = (time.monotonic() - started) * 1000
    finally:
        release.set()

    assert waited_ms < PRICING_DEADLINE_MS + 300
    assert PRICING_HOST in str(raised.value)
    assert "shopper-42" in str(raised.value)
