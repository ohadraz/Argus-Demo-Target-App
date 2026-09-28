from __future__ import annotations

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

Three things worth pinning, and the third is the one the incident rests on. A
service that answers no price fails the page, in words that name the service
rather than the shop. A service that answers slowly does not fail anything - it
produces a line saying where the time went, which is the only evidence a caller
has about a dependency nobody is watching. And a service that does not answer
at all is walked away from, because the shop's own latency cannot be left in
somebody else's hands.
"""


def a_service_answering_in(took_ms: int) -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(
        total_cents=8400, took_ms=took_ms
    )


def a_service_with_no_price() -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(total_cents=None, took_ms=6)


def a_service_that_takes_forever() -> AskThePricingService:
    """The 07:52 dependency, exaggerated so that waiting it out is unmistakable
    in the clock rather than only in the answer.
    """
    def hang(dont_care_shopper: str) -> PricingAnswer:
        time.sleep(5)

        return PricingAnswer(total_cents=8400, took_ms=5000)

    return hang


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


def test_a_slow_answer_that_arrives_in_time_is_still_a_price() -> None:
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


def test_a_service_that_will_not_answer_is_abandoned_at_the_budget() -> None:
    """The incident, in one call. The shop waits the budget and no longer: the
    assertion is on the clock, because an assertion on the answer alone passes
    just as happily against code that waited five seconds for it.
    """
    started = time.monotonic()

    priced = basket_total(
        "shopper-42", a_service_that_takes_forever(), budget_ms=100
    )

    waited_ms = (time.monotonic() - started) * 1000

    assert waited_ms < 1000
    assert priced.total_cents is None


def test_an_abandoned_call_says_so_in_words_that_name_the_service() -> None:
    priced = basket_total(
        "shopper-42", a_service_that_takes_forever(), budget_ms=100
    )

    assert priced.slow_call is not None
    assert PRICING_HOST in priced.slow_call
    assert "shopper-42" in priced.slow_call


def test_the_budget_the_shop_ships_with_is_itself_a_bound() -> None:
    """Not just the budget a test passes in. A default of `None` - or of ten
    seconds - would leave the deployed shop exactly where it was at 07:52.
    """
    started = time.monotonic()

    priced = basket_total("shopper-42", a_service_that_takes_forever())

    waited_ms = (time.monotonic() - started) * 1000

    assert waited_ms < PRICING_BUDGET_MS + 1000
    assert priced.total_cents is None
