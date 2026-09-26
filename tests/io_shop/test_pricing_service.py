from __future__ import annotations

import pytest

from io_shop.pricing_service import (
    CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING,
    PRICING_HOST,
    SHEDDING_SECONDS,
    SLOW_CALL_MS,
    UNACCEPTABLE_CALL_MS,
    AskThePricingService,
    PricingAnswer,
    PricingCircuit,
    PricingServiceFailed,
    basket_total,
)

"""What the shop asks another team's service, and what it says about the asking.

Three things worth pinning, and the last is the one the incident rests on. A
service that answers no price fails the page, in words that name the service
rather than the shop. A service that answers slowly does not fail anything - it
produces a line saying where the time went, which is the only evidence a caller
has about a dependency nobody is watching. And a service that *stays* slow stops
being asked, because a call the shop waits a second and a half for is a worker
it is not serving anybody with.
"""


def a_service_answering_in(took_ms: int) -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(
        total_cents=8400, took_ms=took_ms
    )


def a_service_with_no_price() -> AskThePricingService:
    return lambda dont_care_shopper: PricingAnswer(total_cents=None, took_ms=6)


class CountingService:
    """A pricing service that remembers how often it was actually called.

    The count is the assertion the incident needs: a shed call is invisible in
    the answer alone, and "did not call it" is the whole of the fix.
    """

    def __init__(self, took_ms: int) -> None:
        self.took_ms = took_ms
        self.calls = 0

    def __call__(self, dont_care_shopper: str) -> PricingAnswer:
        self.calls += 1

        return PricingAnswer(total_cents=8400, took_ms=self.took_ms)


class FakeClock:
    """Seconds that move when a test says so, so a cooldown can be tested
    without living through one.
    """

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


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


def test_a_service_that_stays_slow_stops_being_called() -> None:
    # The incident itself: 1500ms a call, every call, until the shop's request
    # pools were full of nothing but waiting. After a run of them the shop stops
    # asking, and the basket comes back without a total rather than without a
    # second and a half.
    circuit = PricingCircuit(now=FakeClock())
    service = CountingService(took_ms=1500)

    for _ in range(CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING):
        basket_total("shopper-42", service, circuit)

    called_before = service.calls
    shed = basket_total("shopper-42", service, circuit)

    assert service.calls == called_before
    assert shed.total_cents is None
    assert shed.shed_call is not None
    assert PRICING_HOST in shed.shed_call
    assert "shopper-42" in shed.shed_call


def test_one_slow_call_short_of_the_run_is_still_called() -> None:
    # A single slow call is noise. Losing the panel over it would be the shop
    # being more fragile than the dependency.
    circuit = PricingCircuit(now=FakeClock())
    service = CountingService(took_ms=1500)

    for _ in range(CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING - 1):
        priced = basket_total("shopper-42", service, circuit)

    assert priced.total_cents == 8400
    assert service.calls == CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING - 1


def test_merely_remarkable_calls_never_shed() -> None:
    # The line in the logs and the decision to stop calling are different
    # thresholds on purpose: a service that has always been worth a log line
    # would otherwise lose its panel for behaving normally.
    circuit = PricingCircuit(now=FakeClock())
    service = CountingService(took_ms=SLOW_CALL_MS)

    for _ in range(CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING * 3):
        priced = basket_total("shopper-42", service, circuit)

    assert priced.total_cents == 8400
    assert priced.slow_call is not None
    assert priced.shed_call is None


def test_an_acceptable_answer_clears_the_run() -> None:
    circuit = PricingCircuit(now=FakeClock())
    slow = CountingService(took_ms=UNACCEPTABLE_CALL_MS)

    for _ in range(CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING - 1):
        basket_total("shopper-42", slow, circuit)

    basket_total("shopper-42", a_service_answering_in(12), circuit)

    for _ in range(CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING - 1):
        priced = basket_total("shopper-42", slow, circuit)

    assert priced.total_cents == 8400
    assert priced.shed_call is None


def test_a_service_that_recovers_is_asked_again_after_the_cooldown() -> None:
    clock = FakeClock()
    circuit = PricingCircuit(now=clock)
    slow = CountingService(took_ms=1500)

    for _ in range(CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING):
        basket_total("shopper-42", slow, circuit)

    assert basket_total("shopper-42", slow, circuit).shed_call is not None

    clock.advance(SHEDDING_SECONDS)
    recovered = basket_total("shopper-42", a_service_answering_in(12), circuit)

    assert recovered.total_cents == 8400
    assert recovered.shed_call is None


def test_a_service_still_slow_after_the_cooldown_sheds_again_on_one_call() -> None:
    # The probe that ends a cooldown is evidence in its own right: a service
    # that is still taking 1500ms should not buy another full run of them.
    clock = FakeClock()
    circuit = PricingCircuit(now=clock)
    service = CountingService(took_ms=1500)

    for _ in range(CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING):
        basket_total("shopper-42", service, circuit)

    clock.advance(SHEDDING_SECONDS)
    probed = basket_total("shopper-42", service, circuit)
    called_after_probe = service.calls
    shed = basket_total("shopper-42", service, circuit)

    assert probed.total_cents == 8400
    assert shed.shed_call is not None
    assert service.calls == called_after_probe


def test_without_a_circuit_every_call_is_made() -> None:
    # A caller with nowhere to keep what it learned gets the behaviour it always
    # had, rather than a circuit that silently forgets between requests.
    service = CountingService(took_ms=1500)

    for _ in range(CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING + 2):
        priced = basket_total("shopper-42", service)

    assert priced.total_cents == 8400
    assert service.calls == CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING + 2
