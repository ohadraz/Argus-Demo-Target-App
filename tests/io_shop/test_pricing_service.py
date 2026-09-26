from __future__ import annotations

import pytest

from io_shop.pricing_service import (
    CALL_BUDGET_MS,
    PRICING_HOST,
    SLOW_CALL_MS,
    AskThePricingService,
    PricingAnswer,
    PricingServiceFailed,
    basket_total,
)

"""What the shop asks another team's service, and what it says about the asking.

Three things worth pinning, and the last two are what the incident rests on. A
service that answers no price fails the page, in words that name the service
rather than the shop. A service that answers slowly does not fail anything - it
produces a line saying where the time went, which is the only evidence a caller
has about a dependency nobody is watching. And a service that does not answer at
all is given a deadline and then abandoned, because a caller without one hands
its own page whatever latency the dependency happens to have.
"""


def a_service_answering_in(took_ms: int) -> AskThePricingService:
    return lambda dont_care_shopper, dont_care_budget: PricingAnswer(
        total_cents=8400, took_ms=took_ms
    )


def a_service_with_no_price() -> AskThePricingService:
    return lambda dont_care_shopper, dont_care_budget: PricingAnswer(
        total_cents=None, took_ms=6
    )


def a_service_that_overruns_its_budget() -> AskThePricingService:
    """A client that gave up when the shop's budget ran out.

    No price, because none arrived - and `timed_out`, because "none arrived"
    and "the service declined to price this" are different facts about the
    world and the page treats them differently.
    """
    return lambda dont_care_shopper, budget_ms: PricingAnswer(
        total_cents=None, took_ms=budget_ms, timed_out=True
    )


def a_service_recording_its_budget(budgets: list[int]) -> AskThePricingService:
    def ask(dont_care_shopper: str, budget_ms: int) -> PricingAnswer:
        budgets.append(budget_ms)

        return PricingAnswer(total_cents=8400, took_ms=12)

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
    priced = basket_total("some-shopper", a_service_answering_in(400))

    assert priced.total_cents == 8400


def test_a_slow_call_names_the_service_the_path_and_the_milliseconds() -> None:
    priced = basket_total("shopper-42", a_service_answering_in(400))

    assert priced.slow_call is not None
    assert PRICING_HOST in priced.slow_call
    assert "400ms" in priced.slow_call
    assert "shopper-42" in priced.slow_call


def test_no_price_fails_in_words_that_name_the_service() -> None:
    with pytest.raises(PricingServiceFailed) as raised:
        basket_total("shopper-42", a_service_with_no_price())

    assert PRICING_HOST in str(raised.value)


def test_the_call_is_made_with_a_budget_and_the_budget_is_bounded() -> None:
    # The whole of the incident in one assertion. The shop hands the deadline
    # to the thing holding the socket, because nothing this module does after a
    # blocking call returns can un-spend the time it took - and the deadline is
    # a number somebody chose, not whatever the dependency happens to take.
    budgets: list[int] = []

    basket_total("some-shopper", a_service_recording_its_budget(budgets))

    assert budgets == [CALL_BUDGET_MS]
    assert SLOW_CALL_MS <= CALL_BUDGET_MS <= 1000


def test_a_service_that_overruns_is_abandoned_rather_than_waited_out() -> None:
    # No price and no exception: the page renders without the basket panel. A
    # dependency at 1500ms costs the shop its budget, not 1500ms of every
    # request.
    priced = basket_total("shopper-42", a_service_that_overruns_its_budget())

    assert priced.total_cents is None
    assert priced.slow_call is not None
    assert PRICING_HOST in priced.slow_call
    assert str(CALL_BUDGET_MS) in priced.slow_call
