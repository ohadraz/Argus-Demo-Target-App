from __future__ import annotations

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

"""What the shop asks another team's service, how long it is prepared to wait,
and what it says about the asking.

Three things worth pinning, and the last two are what the incident rests on. A
service that answers no price fails the page, in words that name the service
rather than the shop. A service that answers slowly does not fail anything - it
produces a line saying where the time went, which is the only evidence a caller
has about a dependency nobody is watching. And a service that does not answer
inside the shop's budget costs one panel rather than the request: the shop
states the budget to whoever holds the socket, because that is the only place a
call can actually be given up on.
"""


def a_service_answering_in(took_ms: int) -> AskThePricingService:
    return lambda dont_care_shopper, dont_care_deadline: PricingAnswer(
        total_cents=8400, took_ms=took_ms
    )


def a_service_with_no_price() -> AskThePricingService:
    return lambda dont_care_shopper, dont_care_deadline: PricingAnswer(
        total_cents=None, took_ms=6
    )


def a_service_that_never_answers_in_time() -> AskThePricingService:
    """A transport that honours the deadline it is handed: it waits exactly the
    budget, gives the call up, and reports no price and the time it spent.
    """
    return lambda dont_care_shopper, deadline_ms: PricingAnswer(
        total_cents=None, took_ms=deadline_ms
    )


def test_a_prompt_answer_is_a_price_and_nothing_to_report() -> None:
    priced = basket_total("some-shopper", a_service_answering_in(12))

    assert priced.total_cents == 8400
    assert priced.slow_call is None
    assert priced.unavailable is None


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


def test_the_shop_tells_the_service_how_long_it_is_willing_to_wait() -> None:
    # The whole of the incident is here. A call made without a budget is a call
    # the shop waits out however long the other side takes, and 1500ms of
    # somebody else's latency becomes 1500ms of Io's on every render. The budget
    # goes to whoever holds the socket, because nowhere else can let it go.
    handed: list[int] = []

    def ask(dont_care_shopper: str, deadline_ms: int) -> PricingAnswer:
        handed.append(deadline_ms)

        return PricingAnswer(total_cents=8400, took_ms=12)

    basket_total("shopper-42", ask)

    assert handed == [PRICING_DEADLINE_MS]
    assert PRICING_DEADLINE_MS <= 1000


def test_a_call_that_spends_the_whole_budget_is_not_the_shops_failure() -> None:
    # The shop gave up on this one, so it is reported rather than raised: no
    # price to show, and the words for why.
    priced = basket_total("shopper-42", a_service_that_never_answers_in_time())

    assert priced.total_cents is None
    assert priced.unavailable is not None
    assert PRICING_HOST in priced.unavailable
    assert str(PRICING_DEADLINE_MS) in priced.unavailable
    assert "shopper-42" in priced.unavailable


def test_a_tighter_budget_is_the_callers_to_set() -> None:
    priced = basket_total(
        "shopper-42", a_service_that_never_answers_in_time(), deadline_ms=250
    )

    assert priced.total_cents is None
    assert priced.unavailable is not None
    assert "250ms" in priced.unavailable
