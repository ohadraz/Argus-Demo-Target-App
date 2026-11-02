from __future__ import annotations

import threading
import time

import pytest

from io_shop.pricing_service import (
    PRICING_BUDGET_MS,
    PRICING_HOST,
    SLOW_CALL_MS,
    AskThePricingService,
    PricingAnswer,
    PricingServiceFailed,
    basket_total,
)

"""What the shop asks another team's service, and what it says about the asking.

Three things worth pinning. A service that answers no price fails the basket,
in words that name the service rather than the shop. A service that answers
slowly but within budget does not fail anything - it produces a line saying
where the time went. And a service that runs past the budget is abandoned, so
its latency is not the account page's.
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


def test_a_slow_answer_within_budget_is_still_a_price() -> None:
    priced = basket_total("some-shopper", a_service_answering_in(PRICING_BUDGET_MS))

    assert priced.total_cents == 8400


def test_a_slow_call_names_the_service_the_path_and_the_milliseconds() -> None:
    priced = basket_total("shopper-42", a_service_answering_in(SLOW_CALL_MS + 50))

    assert priced.slow_call is not None
    assert PRICING_HOST in priced.slow_call
    assert f"{SLOW_CALL_MS + 50}ms" in priced.slow_call
    assert "shopper-42" in priced.slow_call


def test_an_answer_past_the_budget_is_not_waited_for() -> None:
    # The incident: io-pricing took 1500ms and every account page took it too.
    with pytest.raises(PricingServiceFailed) as raised:
        basket_total("shopper-42", a_service_answering_in(1500))

    assert PRICING_HOST in str(raised.value)
    assert "shopper-42" in str(raised.value)


def test_a_call_that_hangs_is_abandoned_at_the_budget() -> None:
    released = threading.Event()

    def a_hanging_service(dont_care_shopper: str) -> PricingAnswer:
        released.wait(5)
        return PricingAnswer(total_cents=8400, took_ms=5000)

    started = time.monotonic()
    try:
        with pytest.raises(PricingServiceFailed) as raised:
            basket_total("shopper-42", a_hanging_service)
        waited_ms = (time.monotonic() - started) * 1000
    finally:
        released.set()

    assert waited_ms < PRICING_BUDGET_MS + 500
    assert PRICING_HOST in str(raised.value)


def test_no_price_fails_in_words_that_name_the_service() -> None:
    with pytest.raises(PricingServiceFailed) as raised:
        basket_total("shopper-42", a_service_with_no_price())

    assert PRICING_HOST in str(raised.value)
