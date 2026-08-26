from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class ScenarioDeploy:
    """A deployment that happened during a scenario's minute.

    Only some scenarios have one - a feature flag flip is not a deploy - and
    that difference is the point: a consumer reading deploy history must be
    able to tell an incident a deploy caused from one it did not.
    """

    revision: str
    repo_url: str
    path: str
    target_revision: str = "main"
    initiated_by: str = "kuki"


@dataclass(frozen=True)
class ScenarioMinute:
    """One minute of a scenario: its log messages and its aggregated metrics.

    Logs and metrics are authored together, per minute, so the two endpoints
    cannot drift apart - `GET /logs` flattens the messages and `GET /metrics`
    emits one bucket, both anchored to the same seed instant. A minute that
    also carries a `deploy` shows up in the Argo CD stand-in's revision history
    at that same minute, for the same reason.
    """

    offset_minutes: int
    messages: tuple[str, ...]
    error_rate: float
    p50_ms: int
    p95_ms: int
    request_volume: int
    deploy: ScenarioDeploy | None = None


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
            # No mention of the deploy. The Argo CD channel is the only place
            # it is recorded, so a diagnosis of BAD_DEPLOYMENT can only have
            # come from there - which is what the e2e case is for.
            messages=(
                "INFO target-service: request succeeded",
                "INFO target-service: request succeeded",
            ),
            error_rate=0.01,
            p50_ms=40,
            p95_ms=220,
            request_volume=1150,
            deploy=ScenarioDeploy(
                revision="9f4c1e7b2a3d5c8e1f0b6a4d2c9e7b5a3f1d8c6e",
                repo_url="https://github.com/kuki/k8s-configs",
                path="apps/target-service/production",
            ),
        ),
        ScenarioMinute(
            offset_minutes=1,
            messages=("WARN target-service: p95 latency climbing",),
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
                "ERROR target-service: request timeout after 5000ms",
                "ERROR target-service: request timeout after 5000ms",
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


# The four models below mirror Argo CD's own wire shape, field names included -
# `repoURL`, `deployedAt`, `targetRevision`. They are deliberately camelCase and
# deliberately not this service's house style: the point of the stand-in is that
# the adapter reading it is the same code that reads a real Argo CD server,
# so
# anything renamed here would be a lie the adapter would have to be written
# around.
class ArgoCdSource(BaseModel):
    repoURL: str  # noqa: N815
    path: str
    targetRevision: str  # noqa: N815


class ArgoCdInitiator(BaseModel):
    username: str


class ArgoCdRevisionHistory(BaseModel):
    id: int
    revision: str
    deployedAt: str  # noqa: N815
    deployStartedAt: str  # noqa: N815
    source: ArgoCdSource
    initiatedBy: ArgoCdInitiator  # noqa: N815


class ArgoCdApplicationMetadata(BaseModel):
    name: str
    namespace: str


class ArgoCdApplicationStatus(BaseModel):
    history: list[ArgoCdRevisionHistory]


class ArgoCdApplication(BaseModel):
    metadata: ArgoCdApplicationMetadata
    status: ArgoCdApplicationStatus


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


@app.get("/argocd/{application}", response_model=ArgoCdApplication)
def argocd_application(application: str) -> ArgoCdApplication:
    """Stands in for Argo CD's `GET /api/v1/applications/{name}`.

    Answers from whichever scenario is seeded, whatever application it is asked
    about - exactly as `/logs` and `/metrics` do - but echoes the requested name
    back in `metadata.name`, because a real Argo CD identifies the application it
    was asked for and an adapter is entitled to rely on that.

    A scenario with no deploy returns an empty history rather than an error: no
    deploy is a real answer, and the whole reason this endpoint exists is to let
    a consumer tell an incident a deploy caused from one it did not.
    """
    metadata = ArgoCdApplicationMetadata(name=application, namespace="argocd")

    if _active_scenario is None or _seeded_at is None:
        return ArgoCdApplication(
            metadata=metadata, status=ArgoCdApplicationStatus(history=[])
        )

    span_minutes = _scenario_span_minutes(_active_scenario)
    history = [
        ArgoCdRevisionHistory(
            id=index,
            revision=entry.deploy.revision,
            deployedAt=_bucket_id(_seeded_at, entry.offset_minutes, span_minutes),
            # A deploy takes a moment; Argo reports when it started as well as
            # when it landed. The minute before is close enough for a fixture,
            # and keeps the two fields distinguishable.
            deployStartedAt=_bucket_id(
                _seeded_at, entry.offset_minutes - 1, span_minutes
            ),
            source=ArgoCdSource(
                repoURL=entry.deploy.repo_url,
                path=entry.deploy.path,
                targetRevision=entry.deploy.target_revision,
            ),
            initiatedBy=ArgoCdInitiator(username=entry.deploy.initiated_by),
        )
        for index, entry in enumerate(SCENARIOS[_active_scenario], start=1)
        if entry.deploy is not None
    ]

    return ArgoCdApplication(
        metadata=metadata, status=ArgoCdApplicationStatus(history=history)
    )


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
