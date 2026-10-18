from __future__ import annotations

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

Three things worth pinning. A service that answers no price fails the page, in
words that name the service rather than the shop. A service that answers slowly
does not fail anything - it produces a line saying where the time went. And a
service slower than the shop's deadline is not waited for: the page goes without
its price rather than every page taking as long as the pricing service does.
"""


def a_service_answering_in(took_ms: int) -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(
        total_cents=8400, took_ms=took_ms
    )


def a_service_with_no_price() -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(total_cents=None, took_ms=6)


def a_service_that_blocks_for(seconds: float) -> AskThePricingService:
    def ask(dont_care_shopper: str) -> PricingAnswer:
        time.sleep(seconds)
        return PricingAnswer(total_cents=8400, took_ms=int(seconds * 1000))

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


def test_a_slow_answer_within_the_deadline_is_still_a_price() -> None:
    priced = basket_total("some-shopper", a_service_answering_in(300))

    assert priced.total_cents == 8400


def test_a_slow_call_names_the_service_the_path_and_the_milliseconds() -> None:
    priced = basket_total("shopper-42", a_service_answering_in(300))

    assert priced.slow_call is not None
    assert PRICING_HOST in priced.slow_call
    assert "300ms" in priced.slow_call
    assert "shopper-42" in priced.slow_call


def test_an_answer_past_the_deadline_is_no_price() -> None:
    # The incident: every call took 1500ms and every page waited for it.
    with pytest.raises(PricingServiceFailed) as raised:
        basket_total("shopper-42", a_service_answering_in(1500))

    assert PRICING_HOST in str(raised.value)
    assert f"{PRICING_DEADLINE_MS}ms" in str(raised.value)


def test_a_service_that_blocks_is_not_waited_for_past_the_deadline() -> None:
    started = time.monotonic()

    with pytest.raises(PricingServiceFailed) as raised:
        basket_total("shopper-42", a_service_that_blocks_for(1.5))

    waited = time.monotonic() - started

    assert waited < 1.0
    assert PRICING_HOST in str(raised.value)


def test_no_price_fails_in_words_that_name_the_service() -> None:
    with pytest.raises(PricingServiceFailed) as raised:
        basket_total("shopper-42", a_service_with_no_price())

    assert PRICING_HOST in str(raised.value)
