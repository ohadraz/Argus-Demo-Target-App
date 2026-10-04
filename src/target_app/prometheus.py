"""The shop's minutes, as Prometheus would answer for them.

Two of Prometheus's surfaces, both in its own shape. The range-query API is
what a responder's tooling asks - `GET /api/v1/query_range` with a PromQL
expression, a window and a step - and it answers a `matrix` envelope. The
scrape endpoint is what Prometheus itself reads off a service - text
exposition, current values only.

A stand-in, not an engine. Nothing here parses PromQL: it knows the fixed set
of expressions the consumer sends, verbatim, and answers each from the field
of the per-minute rows it names. Anything else is refused the way Prometheus
refuses a query it cannot run, so a consumer that changed its expressions
learns it here rather than by reading an empty window as a quiet shop.

Values are the rows' own, digit for digit. That is the point of standing in
rather than computing: a consumer reading through this sees exactly the
numbers it read before, so nothing recorded against the old shape moves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

# The expressions the consumer sends, keyed by the row field each answers.
# Idiomatic PromQL over the series `an_exposition` publishes, so the same
# expressions would run against a real Prometheus scraping this shop. A sample
# at `t` describes the minute that ended at `t`, which is Prometheus's own
# convention for a `[1m]` range.
QUERIES: Final[Mapping[str, str]] = {
    "error_rate": (
        'sum(rate(http_requests_total{code=~"5.."}[1m]))'
        " / sum(rate(http_requests_total[1m]))"
    ),
    "p50_ms": 'max(http_request_duration_seconds{quantile="0.5"}) * 1000',
    "p95_ms": 'max(http_request_duration_seconds{quantile="0.95"}) * 1000',
    "p99_ms": 'max(http_request_duration_seconds{quantile="0.99"}) * 1000',
    "request_volume": "sum(increase(http_requests_total[1m]))",
    "memory_used_bytes": "max(max_over_time(process_resident_memory_bytes[1m]))",
    "memory_limit_bytes": "max(container_spec_memory_limit_bytes)",
    "process_start_time_seconds": "max(process_start_time_seconds)",
    "cpu_used_cores": "sum(rate(container_cpu_usage_seconds_total[1m]))",
    "cpu_limit_cores": 'sum(kube_pod_container_resource_limits{resource="cpu"})',
    "cache_hit_ratio": "avg(cache_hit_ratio)"
}

_FIELD_ANSWERING: Final[Mapping[str, str]] = {
    query: field for field, query in QUERIES.items()
}

# The one step this stand-in evaluates at. A minute is what a row is, and a
# finer step would ask for readings the rows do not hold.
THE_STEP_SECONDS: Final = 60

_A_MINUTE: Final = timedelta(minutes=1)

# The content type Prometheus's text format is served as. Since Prometheus
# 3.0 a scrape without a valid one fails, so it is part of the format.
EXPOSITION_CONTENT_TYPE: Final = "text/plain; version=0.0.4; charset=utf-8"

# Prometheus's own envelope vocabulary.
SUCCESS: Final = "success"
ERROR: Final = "error"
BAD_DATA: Final = "bad_data"
MATRIX: Final = "matrix"


class BadData(Exception):
    """A request Prometheus would answer `400 bad_data` to.

    The message is ours. Prometheus's wording is not reproduced, because a
    consumer that came to depend on a sentence would be depending on a stand-in.
    """


def read_time(value: str) -> datetime:
    """One of Prometheus's two time spellings: RFC 3339, or unix seconds."""
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except ValueError:
        pass

    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise BadData(f"cannot parse {value!r} as a time") from error

    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def read_step(value: str) -> int:
    """A step, as seconds or as a duration like `60s` or `1m`."""
    units = {"s": 1, "m": 60}

    try:
        if value[-1:] in units:
            return int(float(value[:-1]) * units[value[-1]])
        return int(float(value))
    except ValueError as error:
        raise BadData(f"cannot parse {value!r} as a step") from error


def a_matrix(rows: Sequence[Mapping[str, Any]],
             query: str,
             start: datetime,
             end: datetime,
             step_seconds: int,
             now: datetime,
             reporting_lag_minutes: int) -> dict[str, Any]:
    """The `data` of a range query's answer, from the per-minute rows.

    Evaluated at `start`, `start + step` and so on up to `end`, as Prometheus
    evaluates: each step carries the minute that ended at or most recently
    before it, labelled with the step's own time. A step whose minute has no
    row - nothing staged, a minute not published, a field the row does not
    report - carries no sample, and a series with no samples is left out, as
    Prometheus leaves it.

    `reporting_lag_minutes` is whether the minute still in progress is served.
    At 0 it is, at the step its minute ends on, which is how this shop has
    always reported it. At 1 only minutes that have ended are, which is what a
    source that reads a minute once it is over can answer.
    """
    field = _FIELD_ANSWERING.get(query)

    if field is None:
        raise BadData(f"this stand-in does not answer {query!r}")

    if step_seconds != THE_STEP_SECONDS:
        raise BadData(f"this stand-in evaluates at a {THE_STEP_SECONDS}s step only")

    if end < start:
        raise BadData("end is before start")

    ended_by = {
        _the_minute_of(row) + _A_MINUTE: row[field]
        for row in rows
        if row.get(field) is not None
        and (reporting_lag_minutes == 0 or _the_minute_of(row) + _A_MINUTE <= now)
    }
    values = []
    moment = start

    while moment <= end:
        minute_end = moment.replace(second=0, microsecond=0)
        reading = ended_by.get(minute_end)

        if reading is not None:
            values.append([moment.timestamp(), _as_a_sample(reading)])

        moment += timedelta(seconds=step_seconds)

    return {
        "resultType": MATRIX,
        "result": [{"metric": {}, "values": values}] if values else []
    }


def an_exposition(rows: Sequence[Mapping[str, Any]]) -> str:
    """What a scrape of this shop reads: the series the queries name, now.

    Gauges carry the newest minute's reading; counters carry the totals over
    every minute the shop holds, which is as far back as it remembers. Latency
    is a summary - the shop measures quantiles, not buckets, and a histogram
    would be inventing a distribution it never saw.
    """
    if not rows:
        return ""

    newest = rows[-1]
    served = sum(int(row["request_volume"]) for row in rows)
    failed = sum(round(row["error_rate"] * row["request_volume"]) for row in rows)
    cpu_seconds = sum(row["cpu_used_cores"] * 60 for row in rows)

    lines = [
        "# HELP http_requests_total Requests served, by status class.",
        "# TYPE http_requests_total counter",
        f'http_requests_total{{code="200"}} {served - failed}',
        f'http_requests_total{{code="500"}} {failed}',
        "# HELP http_request_duration_seconds How long a request took.",
        "# TYPE http_request_duration_seconds summary",
        *(
            f'http_request_duration_seconds{{quantile="{quantile}"}} '
            f"{newest[field] / 1000}"
            for quantile, field in (("0.5", "p50_ms"),
                                    ("0.95", "p95_ms"),
                                    ("0.99", "p99_ms"))
        ),
        f"http_request_duration_seconds_count {served}",
        "# HELP process_resident_memory_bytes Resident memory size in bytes.",
        "# TYPE process_resident_memory_bytes gauge",
        f"process_resident_memory_bytes {newest['memory_used_bytes']}",
        "# HELP process_start_time_seconds Start time of the process since unix epoch.",
        "# TYPE process_start_time_seconds gauge",
        f"process_start_time_seconds {newest['process_start_time_seconds']}",
        "# HELP container_cpu_usage_seconds_total CPU time consumed.",
        "# TYPE container_cpu_usage_seconds_total counter",
        f"container_cpu_usage_seconds_total {cpu_seconds}"
    ]

    if newest.get("memory_limit_bytes") is not None:
        lines += [
            "# HELP container_spec_memory_limit_bytes Memory limit for the container.",
            "# TYPE container_spec_memory_limit_bytes gauge",
            f"container_spec_memory_limit_bytes {newest['memory_limit_bytes']}"
        ]

    if newest.get("cpu_limit_cores") is not None:
        lines += [
            "# HELP kube_pod_container_resource_limits Resource limits, by resource.",
            "# TYPE kube_pod_container_resource_limits gauge",
            (
                'kube_pod_container_resource_limits{resource="cpu",unit="core"} '
                f"{newest['cpu_limit_cores']}"
            )
        ]

    if newest.get("cache_hit_ratio") is not None:
        lines += [
            "# HELP cache_hit_ratio Share of cache reads that hit.",
            "# TYPE cache_hit_ratio gauge",
            f"cache_hit_ratio {newest['cache_hit_ratio']}"
        ]

    return "\n".join(lines) + "\n"


def _the_minute_of(row: Mapping[str, Any]) -> datetime:
    return read_time(str(row["bucket_id"]))


def _as_a_sample(reading: Any) -> str:
    """A reading as Prometheus spells a sample value: a string, digits kept."""
    return str(reading)
