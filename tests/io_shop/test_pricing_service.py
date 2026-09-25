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

Three things worth pinning, and the last is the one the incident rests on. A
service that answers no price fails the page, in words that name the service
rather than the shop. A service that answers slowly but in time does not fail
anything - it produces a line saying where the time went, which is the only
evidence a caller has about a dependency nobody is watching. And a service that
does not answer in time is abandoned, because the shop waiting however long
pricing takes is how a pricing slowdown turns into an Io latency incident.
"""


def a_service_answering_in(took_ms: int) -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(
        total_cents=8400, took_ms=took_ms
    )


def a_service_with_no_price() -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(total_cents=None, took_ms=6)


def a_service_that_hangs(release: threading.Event) -> AskThePricingService:
    """A service that does not answer until somebody says so.

    Held on an event rather than a sleep so the test never waits on a clock it
    does not control, and released at the end so the worker is not left hanging
    around after the test that borrowed it.
    """
    def ask(dont_care_shopper: str) -> PricingAnswer:
        release.wait(timeout=30)
        return PricingAnswer(total_cents=8400, took_ms=30_000)

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


def test_a_slow_answer_that_still_arrives_in_time_is_a_price() -> None:
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


def test_the_shop_stops_waiting_once_the_budget_is_spent() -> None:
    """The incident, in one test.

    A pricing service that will not answer used to cost the shop however long
    it took - every render, for as long as it lasted. Now it costs the budget.
    """
    release = threading.Event()

    started = time.monotonic()
    try:
        priced = basket_total("shopper-42", a_service_that_hangs(release),
                              budget_ms=50)
    finally:
        release.set()
    waited_ms = (time.monotonic() - started) * 1000

    assert waited_ms < 2000
    assert priced.total_cents is None


def test_an_abandoned_call_says_so_in_words_that_name_the_service() -> None:
    release = threading.Event()

    try:
        priced = basket_total("shopper-42", a_service_that_hangs(release),
                              budget_ms=50)
    finally:
        release.set()

    assert priced.slow_call is not None
    assert PRICING_HOST in priced.slow_call
    assert "shopper-42" in priced.slow_call
    assert "50ms" in priced.slow_call


def test_the_budget_is_generous_enough_for_an_ordinary_answer() -> None:
    """A prompt service is never abandoned, and the default budget says so.

    The budget is a limit on the shop's suffering, not a second latency
    threshold: it sits well above the point at which a call is worth a log line.
    """
    assert PRICING_BUDGET_MS > SLOW_CALL_MS

    priced = basket_total("some-shopper", a_service_answering_in(12))

    assert priced.total_cents == 8400
