from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from target_app.prometheus import (
    EXPOSITION_CONTENT_TYPE,
    MATRIX,
    MEMORY_IN_USE,
    QUERIES,
    THE_STEP_SECONDS,
    BadData,
    a_matrix,
    an_exposition,
    read_step,
    read_time,
    the_reading_for,
)

"""The shop's minutes in Prometheus's shape.

The regression net for a stand-in a consumer's adapter is written against.
Most of it is about what Prometheus would answer - the envelope, the sample
spelling, a sample at a minute's end describing the minute before it - because
a stand-in friendlier than the real service licenses an adapter the real one
then breaks. The rest is about the one thing the stand-in adds: the minute in
progress is served or withheld by the reporting lag.
"""

THE_FIRST_MINUTE = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
A_MINUTE = timedelta(minutes=1)


def a_row(minute: datetime, **fields: Any) -> dict[str, Any]:
    return {
        "bucket_id": minute.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "error_rate": 0.015,
        "p50_ms": 120,
        "p95_ms": 340,
        "p99_ms": 610,
        "request_volume": 1200,
        "memory_used_bytes": 300_000_000,
        "memory_limit_bytes": 512_000_000,
        "process_start_time_seconds": 1759570000.25,
        "cpu_used_cores": 0.75,
        "cpu_limit_cores": 3.0,
        "cache_hit_ratio": None,
        "categoriser_confident_ratio": 0.9,
        **fields
    }


def three_minutes() -> list[dict[str, Any]]:
    return [
        a_row(THE_FIRST_MINUTE, error_rate=0.01),
        a_row(THE_FIRST_MINUTE + A_MINUTE, error_rate=0.2),
        a_row(THE_FIRST_MINUTE + 2 * A_MINUTE, error_rate=0.3)
    ]


def a_matrix_over(rows: list[dict[str, Any]],
                  field: str,
                  *,
                  now: datetime,
                  lag: int = 0) -> dict[str, Any]:
    """Every minute of the rows asked for, the way the consumer asks."""
    return a_matrix(
        rows,
        QUERIES[field],
        start=THE_FIRST_MINUTE + A_MINUTE,
        end=THE_FIRST_MINUTE + 3 * A_MINUTE,
        step_seconds=THE_STEP_SECONDS,
        now=now,
        reporting_lag_minutes=lag
    )


def the_samples(data: dict[str, Any]) -> list[list[Any]]:
    return data["result"][0]["values"] if data["result"] else []


def long_after() -> datetime:
    return THE_FIRST_MINUTE + timedelta(hours=1)


def test_an_answer_is_a_matrix() -> None:
    data = a_matrix_over(three_minutes(), "error_rate", now=long_after())

    assert data["resultType"] == MATRIX
    assert len(data["result"]) == 1


def test_a_sample_at_a_minutes_end_carries_that_minute() -> None:
    # Prometheus's own convention for a `[1m]` range: the value at `t` is about
    # the minute that ended at `t`. Labelled with the minute's start instead, an
    # adapter written against this would be a minute out against the real one.
    samples = the_samples(
        a_matrix_over(three_minutes(), "error_rate", now=long_after())
    )

    assert samples[0] == [(THE_FIRST_MINUTE + A_MINUTE).timestamp(), "0.01"]


def test_every_minute_is_answered_in_order() -> None:
    samples = the_samples(
        a_matrix_over(three_minutes(), "error_rate", now=long_after())
    )

    assert [value for _, value in samples] == ["0.01", "0.2", "0.3"]


def test_values_are_the_rows_digit_for_digit() -> None:
    # What lets a consumer read through this and see the numbers it saw
    # before: an integer stays an integer's spelling, and a float keeps its
    # digits.
    rows = [a_row(THE_FIRST_MINUTE)]

    for field in ("p95_ms", "request_volume", "process_start_time_seconds"):
        [[_, value]] = the_samples(a_matrix_over(rows, field, now=long_after()))

        assert value == str(rows[0][field])


def test_a_field_the_row_does_not_report_has_no_series() -> None:
    # An empty result, as Prometheus answers for a series nobody exposes - not
    # a zero, which would be a reading.
    data = a_matrix_over(three_minutes(), "cache_hit_ratio", now=long_after())

    assert data["result"] == []


def test_a_minute_with_no_row_has_no_sample() -> None:
    # The blind-spot scenario's minutes: not published, so not in the rows, so
    # not answered.
    rows = three_minutes()
    del rows[1]

    samples = the_samples(a_matrix_over(rows, "error_rate", now=long_after()))

    assert [value for _, value in samples] == ["0.01", "0.3"]


def test_at_lag_zero_the_minute_in_progress_is_served() -> None:
    thirty_seconds_into_the_last = THE_FIRST_MINUTE + 2 * A_MINUTE + timedelta(seconds=30)

    samples = the_samples(
        a_matrix_over(three_minutes(), "error_rate", now=thirty_seconds_into_the_last)
    )

    assert len(samples) == 3


def test_at_lag_one_the_minute_in_progress_is_withheld() -> None:
    thirty_seconds_into_the_last = THE_FIRST_MINUTE + 2 * A_MINUTE + timedelta(seconds=30)

    samples = the_samples(
        a_matrix_over(three_minutes(), "error_rate",
                      now=thirty_seconds_into_the_last, lag=1)
    )

    assert [value for _, value in samples] == ["0.01", "0.2"]


def test_at_lag_one_a_minute_is_served_the_instant_it_ends() -> None:
    the_last_minutes_end = THE_FIRST_MINUTE + 3 * A_MINUTE

    samples = the_samples(
        a_matrix_over(three_minutes(), "error_rate", now=the_last_minutes_end, lag=1)
    )

    assert len(samples) == 3


def test_a_query_the_stand_in_does_not_know_is_bad_data() -> None:
    with pytest.raises(BadData):
        a_matrix(three_minutes(), "up", THE_FIRST_MINUTE, THE_FIRST_MINUTE,
                 THE_STEP_SECONDS, long_after(), 0)


def test_a_step_other_than_a_minute_is_bad_data() -> None:
    with pytest.raises(BadData):
        a_matrix(three_minutes(), QUERIES["error_rate"], THE_FIRST_MINUTE,
                 THE_FIRST_MINUTE, 15, long_after(), 0)


def test_an_end_before_the_start_is_bad_data() -> None:
    with pytest.raises(BadData):
        a_matrix(three_minutes(), QUERIES["error_rate"], THE_FIRST_MINUTE + A_MINUTE,
                 THE_FIRST_MINUTE, THE_STEP_SECONDS, long_after(), 0)


def test_a_time_is_read_in_either_of_prometheus_spellings() -> None:
    assert read_time("2026-10-04T12:00:00Z") == THE_FIRST_MINUTE
    assert read_time(str(THE_FIRST_MINUTE.timestamp())) == THE_FIRST_MINUTE


def test_an_unreadable_time_is_bad_data() -> None:
    with pytest.raises(BadData):
        read_time("noon")


def test_a_step_is_read_as_seconds_or_a_duration() -> None:
    assert read_step("60") == 60
    assert read_step("60s") == 60
    assert read_step("1m") == 60


def test_the_exposition_declares_every_series_the_queries_name() -> None:
    exposition = an_exposition(three_minutes())

    for series in ("http_requests_total", "http_request_duration_seconds",
                   "process_resident_memory_bytes", "process_start_time_seconds",
                   "container_cpu_usage_seconds_total",
                   "container_spec_memory_limit_bytes",
                   "kube_pod_container_resource_limits"):
        assert f"# TYPE {series} " in exposition, series


def test_the_exposition_carries_the_newest_minute() -> None:
    rows = three_minutes()
    rows[-1]["memory_used_bytes"] = 444

    assert "process_resident_memory_bytes 444\n" in an_exposition(rows)


def test_an_empty_shop_exposes_nothing() -> None:
    assert an_exposition([]) == ""


def test_the_exposition_is_served_as_prometheus_text() -> None:
    assert EXPOSITION_CONTENT_TYPE.startswith("text/plain; version=0.0.4")


def test_the_categorisers_share_is_answered_from_its_field() -> None:
    rows = [a_row(THE_FIRST_MINUTE, categoriser_confident_ratio=0.4)]

    [[_, value]] = the_samples(
        a_matrix_over(rows, "categoriser_confident_ratio", now=long_after())
    )

    assert value == "0.4"


def test_the_memory_rules_query_is_the_heap_over_its_limit() -> None:
    rows = [a_row(THE_FIRST_MINUTE, memory_used_bytes=256, memory_limit_bytes=1024)]

    data = a_matrix(rows, MEMORY_IN_USE, THE_FIRST_MINUTE + A_MINUTE,
                    THE_FIRST_MINUTE + A_MINUTE, THE_STEP_SECONDS, long_after(), 0)

    assert the_samples(data) == [[(THE_FIRST_MINUTE + A_MINUTE).timestamp(), "0.25"]]


def test_a_query_nobody_answers_has_no_reading() -> None:
    assert the_reading_for("up") is None


def test_the_exposition_carries_the_categorisers_share_where_it_is_reported() -> None:
    rows = three_minutes()
    rows[-1]["categoriser_confident_ratio"] = 0.41

    exposition = an_exposition(rows)

    assert "# TYPE categoriser_confident_ratio gauge" in exposition
    assert "categoriser_confident_ratio 0.41\n" in exposition
