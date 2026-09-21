from __future__ import annotations

from datetime import UTC, datetime, timedelta

from target_app.app import MetricBucket
from target_app.payments import (
    AN_ORDER_IN_CENTS,
    AN_ORDER_IN_EUROCENTS,
    ORDERS_PER_SUCCESSFUL_REQUEST,
    THE_HOME_CURRENCY,
    THE_SECOND_CURRENCY,
    charges_between,
)

"""The takings the shop reports, in a payment provider's shape.

The regression net for an endpoint whose whole job is to move with the
telemetry: a minute in which requests failed is a minute in which orders did
not happen, and an estimate built on takings that ignored the incident would
be measuring the shop's traffic rather than its outage.

Nothing here needs the app, a provider or a clock - charges are derived from
metric buckets, and a bucket is a handful of numbers and a timestamp.
"""

SOME_MINUTE = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

A_WINDOW_AROUND_IT = (SOME_MINUTE - timedelta(minutes=1),
                      SOME_MINUTE + timedelta(minutes=1))

SOME_VOLUME = 1_000
NOTHING_FAILED = 0.0

# Takings come from traffic and failures. What the shop's memory was doing, and
# how slow its slowest requests were, are required on a bucket and decide
# nothing here.
DONT_CARE_MEMORY_BYTES = 400 * 1024**2
DONT_CARE_P99_MS = 380
DONT_CARE_STARTED_AT = SOME_MINUTE.timestamp()


def test_a_minute_of_traffic_becomes_orders_at_the_shops_conversion_rate() -> None:
    charges = charges_between(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        *A_WINDOW_AROUND_IT,
    )

    assert len(charges) == int(SOME_VOLUME * ORDERS_PER_SUCCESSFUL_REQUEST)


def test_a_minute_in_which_requests_failed_takes_proportionally_less() -> None:
    # The point of the endpoint. Half the requests failing has to halve the
    # takings, or an incident costs nothing and every figure resting on this
    # is measuring traffic rather than an outage.
    half_of_them_failed = 0.5

    calm = charges_between(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        *A_WINDOW_AROUND_IT,
    )
    broken = charges_between(
        [_a_minute(error_rate=half_of_them_failed, request_volume=SOME_VOLUME)],
        *A_WINDOW_AROUND_IT,
    )

    assert len(broken) == len(calm) // 2


def test_the_shop_is_paid_in_two_currencies() -> None:
    # A consumer that sums a window without reading the currency gets a figure
    # that is wrong rather than obviously broken, which is the mistake worth
    # making impossible to miss.
    charges = charges_between(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        *A_WINDOW_AROUND_IT,
    )

    currencies = {charge["currency"] for charge in charges}
    amounts = {charge["currency"]: charge["amount"] for charge in charges}

    assert currencies == {THE_HOME_CURRENCY, THE_SECOND_CURRENCY}
    assert amounts[THE_HOME_CURRENCY] == AN_ORDER_IN_CENTS
    assert amounts[THE_SECOND_CURRENCY] == AN_ORDER_IN_EUROCENTS


def test_the_home_currency_is_the_majority_of_the_takings() -> None:
    charges = charges_between(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        *A_WINDOW_AROUND_IT,
    )

    at_home = [c for c in charges if c["currency"] == THE_HOME_CURRENCY]

    assert len(at_home) > len(charges) - len(at_home)


def test_minutes_outside_the_window_are_not_charged_for() -> None:
    long_before = SOME_MINUTE - timedelta(hours=2)

    charges = charges_between(
        [
            _a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME),
            _a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME,
                      at=long_before),
        ],
        *A_WINDOW_AROUND_IT,
    )

    assert all(charge["created"] >= int(SOME_MINUTE.timestamp())
               for charge in charges)


def test_a_charge_carries_the_fields_a_payment_provider_reports() -> None:
    # The wire shape is the contract: an adapter written against the real
    # provider reads these names, and a stand-in renaming any of them would be
    # a lie that adapter has to be written around.
    charge = charges_between(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        *A_WINDOW_AROUND_IT,
    )[0]

    assert charge["object"] == "charge"
    assert charge["status"] == "succeeded"
    assert charge["amount_refunded"] == 0
    assert charge["livemode"] is False
    assert isinstance(charge["created"], int)
    assert charge["id"].startswith("ch_")


def test_the_same_window_answers_the_same_twice() -> None:
    buckets = [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)]

    assert (charges_between(buckets, *A_WINDOW_AROUND_IT)
            == charges_between(buckets, *A_WINDOW_AROUND_IT))


def _a_minute(error_rate: float,
              request_volume: int,
              at: datetime = SOME_MINUTE) -> MetricBucket:
    return MetricBucket(
        bucket_id=at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        error_rate=error_rate,
        p50_ms=40,
        p95_ms=90,
        p99_ms=DONT_CARE_P99_MS,
        request_volume=request_volume,
        memory_used_bytes=DONT_CARE_MEMORY_BYTES,
        process_start_time_seconds=DONT_CARE_STARTED_AT,
    )
