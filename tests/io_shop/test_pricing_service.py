from __future__ import annotations

import pytest

from io_shop.pricing_service import (
    BAD_CALLS_BEFORE_PAUSING,
    PAUSE_SECONDS,
    PRICING_HOST,
    SLOW_CALL_MS,
    TOO_SLOW_MS,
    AskThePricingService,
    PricingAnswer,
    PricingServiceFailed,
    PricingServiceHealth,
    basket_total,
    forget_pricing_health,
)

"""What the shop asks another team's service, and what it says about the asking.

Three things worth pinning, and the third is the one the incident rests on. A
service that answers no price fails the page, in words that name the service
rather than the shop. A service that answers slowly does not fail anything - it
produces a line saying where the time went, which is the only evidence a caller
has about a dependency nobody is watching. And a service that keeps answering
slowly stops being called, because a dependency's latency that every render pays
is the shop's latency.
"""


@pytest.fixture(autouse=True)
def a_shop_with_no_opinion_about_pricing() -> None:
    """Every case begins holding nothing about the service.

    What the shop has lately observed is process state, so without this each
    case would inherit the last one's slow calls.
    """
    forget_pricing_health()


class AClockThatOnlyMovesWhenTold:
    """Time as a test states it, so that thirty seconds need not be spent."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def move_on(self, seconds: float) -> None:
        self.seconds += seconds


class AServiceThatCounts:
    """A pricing service that answers in a fixed time and remembers how often
    it was actually asked - which is the measurement this file exists for."""

    def __init__(self, took_ms: int) -> None:
        self.took_ms = took_ms
        self.calls = 0

    def __call__(self, dont_care_shopper: str) -> PricingAnswer:
        self.calls += 1

        return PricingAnswer(total_cents=8400, took_ms=self.took_ms)


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


def test_a_service_that_stays_slow_stops_being_waited_on() -> None:
    """The incident, stated as a bound on work rather than on the answer.

    Twenty renders against a service answering in 1500ms must not cost twenty
    calls of 1500ms. After a few of them the shop stops asking, and everything
    after that costs nothing at all - which is the difference between one slow
    dependency and a slow shop.
    """
    service = AServiceThatCounts(took_ms=1500)
    health = PricingServiceHealth(now=AClockThatOnlyMovesWhenTold())

    for _ in range(20):
        basket_total("shopper-41829-9-0", service, health)

    assert service.calls <= BAD_CALLS_BEFORE_PAUSING


def test_a_render_during_the_pause_costs_nothing_and_says_so() -> None:
    # No total, because the shop does not price baskets itself - but a line
    # naming the service and the pause, so the missing figure is accounted for
    # in the logs rather than being a mystery on the page.
    service = AServiceThatCounts(took_ms=1500)
    health = PricingServiceHealth(now=AClockThatOnlyMovesWhenTold())

    for _ in range(BAD_CALLS_BEFORE_PAUSING):
        basket_total("shopper-8079-2-0", service, health)

    calls_before = service.calls
    paused = basket_total("shopper-8079-2-0", service, health)

    assert service.calls == calls_before
    assert paused.total_cents is None
    assert paused.slow_call is not None
    assert PRICING_HOST in paused.slow_call
    assert "shopper-8079-2-0" in paused.slow_call


def test_the_shop_tries_again_once_the_pause_has_run_out() -> None:
    # A pause, not a ban. The service gets asked again, and a service that has
    # recovered is priced from again immediately.
    clock = AClockThatOnlyMovesWhenTold()
    health = PricingServiceHealth(now=clock)
    slow = AServiceThatCounts(took_ms=1500)

    for _ in range(BAD_CALLS_BEFORE_PAUSING):
        basket_total("some-shopper", slow, health)

    clock.move_on(PAUSE_SECONDS + 1)
    recovered = AServiceThatCounts(took_ms=12)
    priced = basket_total("some-shopper", recovered, health)

    assert recovered.calls == 1
    assert priced.total_cents == 8400
    assert priced.slow_call is None


def test_a_service_merely_worth_remarking_on_is_still_called() -> None:
    # The two thresholds are not the same decision. A service sitting just over
    # the logging threshold is written about, never shed - otherwise the first
    # line a reader ever saw would also be the last.
    service = AServiceThatCounts(took_ms=TOO_SLOW_MS - 1)
    health = PricingServiceHealth(now=AClockThatOnlyMovesWhenTold())

    for _ in range(20):
        priced = basket_total("some-shopper", service, health)

        assert priced.total_cents == 8400
        assert priced.slow_call is not None

    assert service.calls == 20


def test_one_slow_answer_among_prompt_ones_changes_nothing() -> None:
    # Weather, not an outage. A shop that stopped calling after a single slow
    # answer would shed a healthy dependency on the strength of one packet.
    health = PricingServiceHealth(now=AClockThatOnlyMovesWhenTold())

    for took_ms in (12, 1500, 12, 1500, 12):
        priced = basket_total("some-shopper", a_service_answering_in(took_ms), health)

        assert priced.total_cents == 8400
