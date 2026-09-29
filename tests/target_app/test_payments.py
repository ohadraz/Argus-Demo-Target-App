from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from target_app.app import MetricBucket
from target_app.generator import BASELINE_ERROR_RATE, REPORTED_VOLUME_PER_MINUTE
from target_app.payments import (
    AN_ORDER_IN_CENTS,
    AN_ORDER_IN_EUROCENTS,
    ORDERS_PER_SUCCESSFUL_REQUEST,
    THE_HOME_CURRENCY,
    THE_SECOND_CURRENCY,
    a_page_of_charges,
)

"""The takings the shop reports, in a payment provider's shape.

The regression net for an endpoint with two jobs. It has to move with the
telemetry: a minute in which requests failed is a minute in which orders did
not happen, and an estimate built on takings that ignored the incident would be
measuring the shop's traffic rather than its outage.

And it has to answer a window of any age, because the provider it stands in for
does. Stripe does not expire charges, so a window older than this shop's metrics
reach is an ordinary window there - and a stand-in answering it empty would
teach a consumer that no takings and no records are the same answer. They are
not, and conflating them cost a postmortem a fabricated loss of nothing.

Nothing here needs the app, a provider or a clock.
"""

SOME_MINUTE = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

# Exactly the reported minute, so a count below is that minute's orders and not
# also the ordinary trade of the minutes either side of it.
JUST_THAT_MINUTE = (SOME_MINUTE, SOME_MINUTE)

SOME_VOLUME = 1_000
NOTHING_FAILED = 0.0

A_FULL_PAGE = 100

# Takings come from traffic and failures. What the shop's memory was doing, and
# how slow its slowest requests were, are required on a bucket and decide
# nothing here.
DONT_CARE_MEMORY_BYTES = 400 * 1024**2
DONT_CARE_CPU_CORES = 0.75
DONT_CARE_P99_MS = 380
DONT_CARE_STARTED_AT = SOME_MINUTE.timestamp()


def every_charge_in(buckets: list[MetricBucket],
                    window: tuple[datetime, datetime]) -> list[dict[str, Any]]:
    """The whole window, paged to its end the way a client pages it.

    Through the pages rather than around them, because the pages are the only
    way in: a window is answered a hundred charges at a time, and a helper that
    reached past that would be testing something no consumer can reach.
    """
    charges: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        page, more = a_page_of_charges(buckets, *window, A_FULL_PAGE, cursor)
        charges += page

        if not more:
            return charges

        cursor = page[-1]["id"]


def test_a_minute_of_traffic_becomes_orders_at_the_shops_conversion_rate() -> None:
    charges = every_charge_in(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        JUST_THAT_MINUTE,
    )

    assert len(charges) == int(SOME_VOLUME * ORDERS_PER_SUCCESSFUL_REQUEST)


def test_a_minute_in_which_requests_failed_takes_proportionally_less() -> None:
    # The point of the endpoint. Half the requests failing has to halve the
    # takings, or an incident costs nothing and every figure resting on this
    # is measuring traffic rather than an outage.
    half_of_them_failed = 0.5

    calm = every_charge_in(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        JUST_THAT_MINUTE,
    )
    broken = every_charge_in(
        [_a_minute(error_rate=half_of_them_failed, request_volume=SOME_VOLUME)],
        JUST_THAT_MINUTE,
    )

    assert len(broken) == len(calm) // 2


def test_a_minute_the_metrics_no_longer_reach_still_took_its_ordinary_trade() -> None:
    # The provider does not expire charges, so a window older than this shop's
    # metrics is not a quiet window - it is a window like any other, and the shop
    # was trading normally through it because nothing was wrong then.
    charges = every_charge_in([], JUST_THAT_MINUTE)

    assert len(charges) == int(
        REPORTED_VOLUME_PER_MINUTE * (1 - BASELINE_ERROR_RATE)
        * ORDERS_PER_SUCCESSFUL_REQUEST
    )


def test_a_reported_minute_is_charged_for_as_reported_rather_than_as_ordinary(
) -> None:
    # Where the metrics still hold the minute, that reading wins. Otherwise an
    # incident would be costed against the trade of a shop that was well, which
    # is the whole thing this endpoint exists to prevent.
    everything_failed = 1.0

    charges = every_charge_in(
        [_a_minute(error_rate=everything_failed, request_volume=SOME_VOLUME)],
        JUST_THAT_MINUTE,
    )

    assert charges == []


def test_the_shop_is_paid_in_two_currencies() -> None:
    # A consumer that sums a window without reading the currency gets a figure
    # that is wrong rather than obviously broken, which is the mistake worth
    # making impossible to miss.
    charges = every_charge_in(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        JUST_THAT_MINUTE,
    )

    currencies = {charge["currency"] for charge in charges}
    amounts = {charge["currency"]: charge["amount"] for charge in charges}

    assert currencies == {THE_HOME_CURRENCY, THE_SECOND_CURRENCY}
    assert amounts[THE_HOME_CURRENCY] == AN_ORDER_IN_CENTS
    assert amounts[THE_SECOND_CURRENCY] == AN_ORDER_IN_EUROCENTS


def test_the_home_currency_is_the_majority_of_the_takings() -> None:
    charges = every_charge_in(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        JUST_THAT_MINUTE,
    )

    at_home = [c for c in charges if c["currency"] == THE_HOME_CURRENCY]

    assert len(at_home) > len(charges) - len(at_home)


def test_minutes_outside_the_window_are_not_charged_for() -> None:
    charges = every_charge_in(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        JUST_THAT_MINUTE,
    )

    assert all(
        int(SOME_MINUTE.timestamp())
        <= charge["created"]
        < int((SOME_MINUTE + timedelta(minutes=1)).timestamp())
        for charge in charges
    )


def test_a_charge_carries_the_fields_a_payment_provider_reports() -> None:
    # The wire shape is the contract: an adapter written against the real
    # provider reads these names, and a stand-in renaming any of them would be
    # a lie that adapter has to be written around.
    charge = every_charge_in(
        [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)],
        JUST_THAT_MINUTE,
    )[0]

    assert charge["object"] == "charge"
    assert charge["status"] == "succeeded"
    assert charge["amount_refunded"] == 0
    assert charge["livemode"] is False
    assert isinstance(charge["created"], int)
    assert charge["id"].startswith("ch_")


def test_the_same_window_answers_the_same_twice() -> None:
    buckets = [_a_minute(error_rate=NOTHING_FAILED, request_volume=SOME_VOLUME)]

    assert (every_charge_in(buckets, JUST_THAT_MINUTE)
            == every_charge_in(buckets, JUST_THAT_MINUTE))


def test_a_page_stops_at_the_limit_and_says_more_follow() -> None:
    an_hour = (SOME_MINUTE, SOME_MINUTE + timedelta(hours=1))

    page, more = a_page_of_charges([], *an_hour, A_FULL_PAGE)

    assert len(page) == A_FULL_PAGE
    assert more is True


def test_the_last_page_says_nothing_follows() -> None:
    page, more = a_page_of_charges([], *JUST_THAT_MINUTE, 10_000)

    assert page
    assert more is False


def test_paging_repeats_no_charge_and_skips_none() -> None:
    # The cursor is read back out of the charge's own id rather than searched
    # for, so this is the case that says the arithmetic inverts correctly.
    an_hour = (SOME_MINUTE, SOME_MINUTE + timedelta(hours=1))

    paged = every_charge_in([], an_hour)

    assert len(paged) == len({charge["id"] for charge in paged})


def test_the_window_is_listed_newest_first() -> None:
    # The provider's own order - "the most recent charges appearing first" - and
    # the reason a cursor means what it means. Listed the other way round, a
    # consumer that reads only the first page reads the oldest charges in the
    # window while believing it read the newest.
    an_hour = (SOME_MINUTE, SOME_MINUTE + timedelta(hours=1))

    paged = every_charge_in([], an_hour)

    assert paged == sorted(
        paged, key=lambda charge: charge["created"], reverse=True
    )


def test_a_cursor_from_nowhere_answers_from_the_start_of_the_window() -> None:
    # A caller's mistake, and the first page is the answer least likely to be
    # quietly wrong: a window silently emptied by an unreadable cursor would
    # look exactly like a shop that took nothing.
    from_the_start, _ = a_page_of_charges([], *JUST_THAT_MINUTE, A_FULL_PAGE)

    with_nonsense, _ = a_page_of_charges(
        [], *JUST_THAT_MINUTE, A_FULL_PAGE, "not-a-charge-id"
    )

    assert with_nonsense == from_the_start


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
        cpu_used_cores=DONT_CARE_CPU_CORES,
        process_start_time_seconds=DONT_CARE_STARTED_AT,
    )
