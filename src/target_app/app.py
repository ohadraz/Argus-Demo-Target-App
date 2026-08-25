from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class ScenarioMinute:
    """One minute of a scenario: its log messages and its aggregated metrics.

    Logs and metrics are authored together, per minute, so the two endpoints
    cannot drift apart - `GET /logs` flattens the messages and `GET /metrics`
    emits one bucket, both anchored to the same seed instant.
    """

    offset_minutes: int
    messages: tuple[str, ...]
    error_rate: float
    p50_ms: int
    p95_ms: int
    request_volume: int


# feature-flag-toggle spikes the error rate while latency stays flat;
# bad-deployment spikes p95 latency while the error rate stays mild. The two
# failure modes are distinguishable from the metrics alone.
SCENARIOS: dict[str, tuple[ScenarioMinute, ...]] = {
    "feature-flag-toggle": (
        ScenarioMinute(
            offset_minutes=0,
            messages=(
                "INFO target-service: feature flag 'checkout-v2' is off, request succeeded",
                "INFO target-service: feature flag 'checkout-v2' is off, request succeeded",
            ),
            error_rate=0.01,
            p50_ms=45,
            p95_ms=210,
            request_volume=1200,
        ),
        ScenarioMinute(
            offset_minutes=1,
            messages=(
                "WARN target-service: feature flag 'checkout-v2' toggled from 'off' to 'on'",
            ),
            error_rate=0.03,
            p50_ms=47,
            p95_ms=215,
            request_volume=1180,
        ),
        ScenarioMinute(
            offset_minutes=2,
            messages=(
                "ERROR target-service: request failed - feature flag 'checkout-v2' is on, error rate elevated",
                "ERROR target-service: request failed - feature flag 'checkout-v2' is on, error rate elevated",
            ),
            error_rate=0.38,
            p50_ms=46,
            p95_ms=220,
            request_volume=1210,
        ),
        ScenarioMinute(
            offset_minutes=3,
            messages=(
                "ERROR target-service: request failed - feature flag 'checkout-v2' is on, error rate at 41% over the last minute",
            ),
            error_rate=0.41,
            p50_ms=48,
            p95_ms=225,
            request_volume=1195,
        ),
    ),
    "bad-deployment": (
        ScenarioMinute(
            offset_minutes=0,
            messages=(
                "INFO target-service: deploy started, version 1.4.2 -> 1.4.3",
                "INFO target-service: deploy completed, version 1.4.3 live",
            ),
            error_rate=0.01,
            p50_ms=40,
            p95_ms=220,
            request_volume=1150,
        ),
        ScenarioMinute(
            offset_minutes=1,
            messages=("WARN target-service: p95 latency climbing since deploy of version 1.4.3",),
            error_rate=0.02,
            p50_ms=95,
            p95_ms=900,
            request_volume=1120,
        ),
        ScenarioMinute(
            offset_minutes=2,
            messages=("WARN target-service: p95 latency at 1800ms, up from a 220ms baseline",),
            error_rate=0.04,
            p50_ms=180,
            p95_ms=1800,
            request_volume=1090,
        ),
        ScenarioMinute(
            offset_minutes=3,
            messages=(
                "ERROR target-service: request timeout after 5000ms, version 1.4.3",
                "ERROR target-service: request timeout after 5000ms, version 1.4.3",
            ),
            error_rate=0.12,
            p50_ms=320,
            p95_ms=5000,
            request_volume=1050,
        ),
    ),
}

# In-memory only: a restart clears these back to None. The scenario content
# above is code, not state, so it survives restarts unaffected.
_active_scenario: str | None = None
# The instant the active scenario was seeded, truncated to the minute. Every
# log timestamp and metric bucket id is this plus the entry's offset, so a
# freshly seeded scenario always reads as recent instead of drifting into the
# past the way baked-in absolute timestamps would.
_seeded_at: datetime | None = None


class SeedRequest(BaseModel):
    scenario_id: str


class ScenarioStatus(BaseModel):
    active_scenario: str | None


class MetricBucket(BaseModel):
    bucket_id: str
    error_rate: float
    p50_ms: int
    p95_ms: int
    request_volume: int


def _scenario_span_minutes(scenario_id: str) -> int:
    return max(entry.offset_minutes for entry in SCENARIOS[scenario_id])


def _bucket_id(seeded_at: datetime, offset_minutes: int, span_minutes: int) -> str:
    """Format one scenario minute as a bucket id, anchored so the scenario's
    *last* minute is the seed instant.

    The incident has therefore already happened by the time anything asks
    about it, which is the only way round it can be: a consumer windowing its
    retrieval will end that window at "now", because no minute after now
    exists to be read. Anchoring the scenario's *first* minute at the seed
    instant would put the rest of the incident in the future, where a correct
    reader cannot see it.

    The same string prefixes that minute's log lines, so a caller can match a
    bucket to its entries without re-parsing either.
    """
    minute = seeded_at.replace(second=0, microsecond=0) + timedelta(
        minutes=offset_minutes - span_minutes
    )
    return minute.strftime(TIMESTAMP_FORMAT)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/scenario/seed", response_model=ScenarioStatus)
def seed_scenario(body: SeedRequest) -> ScenarioStatus:
    global _active_scenario, _seeded_at
    if body.scenario_id not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"unknown scenario id: {body.scenario_id}")
    _active_scenario = body.scenario_id
    _seeded_at = datetime.now(UTC).replace(second=0, microsecond=0)
    return ScenarioStatus(active_scenario=_active_scenario)


@app.post("/scenario/reset", response_model=ScenarioStatus)
def reset_scenario() -> ScenarioStatus:
    global _active_scenario, _seeded_at
    _active_scenario = None
    _seeded_at = None
    return ScenarioStatus(active_scenario=_active_scenario)


@app.get("/scenario/status", response_model=ScenarioStatus)
def scenario_status() -> ScenarioStatus:
    return ScenarioStatus(active_scenario=_active_scenario)


@app.get("/logs", response_model=list[str])
def logs() -> list[str]:
    if _active_scenario is None or _seeded_at is None:
        return []
    span_minutes = _scenario_span_minutes(_active_scenario)
    return [
        f"{_bucket_id(_seeded_at, entry.offset_minutes, span_minutes)} {message}"
        for entry in SCENARIOS[_active_scenario]
        for message in entry.messages
    ]


@app.get("/metrics", response_model=list[MetricBucket])
def metrics() -> list[MetricBucket]:
    if _active_scenario is None or _seeded_at is None:
        return []
    span_minutes = _scenario_span_minutes(_active_scenario)
    return [
        MetricBucket(
            bucket_id=_bucket_id(_seeded_at, entry.offset_minutes, span_minutes),
            error_rate=entry.error_rate,
            p50_ms=entry.p50_ms,
            p95_ms=entry.p95_ms,
            request_volume=entry.request_volume,
        )
        for entry in SCENARIOS[_active_scenario]
    ]
