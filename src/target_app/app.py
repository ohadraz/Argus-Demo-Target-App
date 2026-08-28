from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from target_app import console
from target_app.flags import FlagClient, FlagProviderUnavailable
from target_app.generator import GeneratedMinute, generate
from target_app.scenarios import (
    SCENARIOS,
    TIMESTAMP_FORMAT,
    Scenario,
    bucket_id,
    scenario_span_minutes,
)
from target_app.settings import get_unleash_settings
from target_app.state import ScenarioState

# How much history the generated channels serve. Wide enough that a reader
# looking for the service's calm baseline finds plenty of it either side of an
# incident, and narrow enough that generating it stays cheap.
GENERATED_SPAN_MINUTES = 90

flags = FlagClient()
state = ScenarioState(flags)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Makes sure the flag this service reads exists, and is unambiguous, before
    serving.

    Waits for the provider rather than assuming it: compose ordering already
    holds this container back until the provider is healthy, but a service
    started by hand has no such promise, and crash-looping against a provider
    that is thirty seconds from ready helps nobody.
    """
    flags.wait_until_reachable()
    flags.ensure_only_one_environment()
    flags.ensure_flag_exists()
    yield


app = FastAPI(lifespan=lifespan)

# The shop's own artwork. Served from a directory rather than inlined so the
# image can be edited as an image, and mounted from a path relative to this
# module so it resolves the same in the container as in a local checkout.
app.mount(
    "/assets",
    StaticFiles(directory=Path(__file__).parent / "assets"),
    name="assets",
)


class SeedRequest(BaseModel):
    scenario_id: str


class ScenarioStatus(BaseModel):
    active_scenario: str | None


class ScenarioSummary(BaseModel):
    id: str
    title: str
    description: str
    is_generated: bool


class ScenarioCatalog(BaseModel):
    scenarios: list[ScenarioSummary]
    active_scenario: str | None
    flag: str
    flag_is_on: bool
    phase: str
    # The minute the incident stopped, once it has. A consumer marks it rather
    # than inferring it: the drop is visible in the numbers, but which minute
    # *caused* the drop is a fact this service holds and a reader would only be
    # guessing at.
    recovered_at: str | None


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
# so anything renamed here would be a lie the adapter would have to be written
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def operator_console() -> str:
    """The operator's view of this service - see `target_app.console`."""
    return console.PAGE


@app.get("/scenario/catalog", response_model=ScenarioCatalog)
def scenario_catalog() -> ScenarioCatalog:
    """What can be staged, what is staged, and what the flag actually reads.

    The flag's live state is here rather than left implicit because a scenario
    left running by an earlier session is otherwise invisible: the in-memory
    active-scenario record clears on restart while the flag, which lives
    somewhere else entirely, does not.
    """
    return ScenarioCatalog(
        scenarios=[
            ScenarioSummary(
                id=scenario.id,
                title=scenario.title,
                description=scenario.description,
                is_generated=scenario.is_generated,
            )
            for scenario in SCENARIOS.values()
        ],
        active_scenario=state.active_scenario_id,
        flag=get_unleash_settings().flag,
        flag_is_on=_flag_is_on(),
        phase=state.phase(),
        recovered_at=_recovered_at(),
    )


def _recovered_at() -> str | None:
    window = state.generated_window()

    if window is None or window[0].turned_off_at is None:
        return None

    return window[0].turned_off_at.replace(second=0, microsecond=0).strftime(
        TIMESTAMP_FORMAT
    )


@app.post("/scenario/seed", response_model=ScenarioStatus)
def seed_scenario(body: SeedRequest) -> ScenarioStatus:
    if body.scenario_id not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"unknown scenario id: {body.scenario_id}")

    try:
        state.seed(SCENARIOS[body.scenario_id])
    except FlagProviderUnavailable as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ScenarioStatus(active_scenario=state.active_scenario_id)


@app.post("/scenario/reset", response_model=ScenarioStatus)
def reset_scenario() -> ScenarioStatus:
    try:
        state.reset()
    except FlagProviderUnavailable as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ScenarioStatus(active_scenario=state.active_scenario_id)


@app.get("/scenario/status", response_model=ScenarioStatus)
def scenario_status() -> ScenarioStatus:
    return ScenarioStatus(active_scenario=state.active_scenario_id)


@app.get("/logs", response_model=list[str])
def logs() -> list[str]:
    active = state.active

    if active is None:
        return []

    if active.scenario.is_generated:
        return [
            line
            for minute in _generated_minutes()
            for line in minute.log_lines
        ]

    return _authored_log_lines(active.scenario, active.seeded_at)


@app.get("/metrics", response_model=list[MetricBucket])
def metrics() -> list[MetricBucket]:
    active = state.active

    if active is None:
        return []

    if active.scenario.is_generated:
        return [
            MetricBucket(
                bucket_id=minute.minute_id,
                error_rate=minute.error_rate,
                p50_ms=minute.p50_ms,
                p95_ms=minute.p95_ms,
                request_volume=minute.request_volume,
            )
            for minute in _generated_minutes()
        ]

    return _authored_metrics(active.scenario, active.seeded_at)


@app.get("/argocd/{application}", response_model=ArgoCdApplication)
def argocd_application(application: str) -> ArgoCdApplication:
    """Stands in for Argo CD's `GET /api/v1/applications/{name}`.

    Answers from whichever scenario is seeded, whatever application it is asked
    about - exactly as `/logs` and `/metrics` do - but echoes the requested name
    back in `metadata.name`, because a real Argo CD identifies the application it
    was asked for and an adapter is entitled to rely on that.

    A scenario with no deploy returns an empty history rather than an error: no
    deploy is a real answer, and the whole reason this endpoint exists is to let
    a consumer tell an incident a deploy caused from one it did not. A generated
    scenario has no deploys at all, and answers the same way.
    """
    metadata = ArgoCdApplicationMetadata(name=application, namespace="argocd")
    active = state.active

    if active is None or active.seeded_at is None or active.scenario.is_generated:
        return ArgoCdApplication(
            metadata=metadata, status=ArgoCdApplicationStatus(history=[])
        )

    span_minutes = scenario_span_minutes(active.scenario)
    history = [
        ArgoCdRevisionHistory(
            id=index,
            revision=entry.deploy.revision,
            deployedAt=bucket_id(active.seeded_at, entry.offset_minutes, span_minutes),
            # A deploy takes a moment; Argo reports when it started as well as
            # when it landed. The minute before is close enough for a fixture,
            # and keeps the two fields distinguishable.
            deployStartedAt=bucket_id(
                active.seeded_at, entry.offset_minutes - 1, span_minutes
            ),
            source=ArgoCdSource(
                repoURL=entry.deploy.repo_url,
                path=entry.deploy.path,
                targetRevision=entry.deploy.target_revision,
            ),
            initiatedBy=ArgoCdInitiator(username=entry.deploy.initiated_by),
        )
        for index, entry in enumerate(active.scenario.minutes, start=1)
        if entry.deploy is not None
    ]

    return ArgoCdApplication(
        metadata=metadata, status=ArgoCdApplicationStatus(history=history)
    )


def _generated_minutes() -> list[GeneratedMinute]:
    """The generated window, run up to whatever instant the scenario has
    reached.

    That instant is `now` while the incident is live and for a settling period
    after it recovers, and stops moving afterwards - which is how a finished
    scenario stops without being cleared.
    """
    window = state.generated_window()

    if window is None:
        return []

    timeline, up_to = window
    return generate(timeline, up_to, GENERATED_SPAN_MINUTES)


def _authored_log_lines(scenario: Scenario, seeded_at: datetime | None) -> list[str]:
    if seeded_at is None:
        return []

    span_minutes = scenario_span_minutes(scenario)
    return [
        f"{bucket_id(seeded_at, entry.offset_minutes, span_minutes)} {message}"
        for entry in scenario.minutes
        for message in entry.messages
    ]


def _authored_metrics(scenario: Scenario, seeded_at: datetime | None) -> list[MetricBucket]:
    if seeded_at is None:
        return []

    span_minutes = scenario_span_minutes(scenario)
    return [
        MetricBucket(
            bucket_id=bucket_id(seeded_at, entry.offset_minutes, span_minutes),
            error_rate=entry.error_rate,
            p50_ms=entry.p50_ms,
            p95_ms=entry.p95_ms,
            request_volume=entry.request_volume,
        )
        for entry in scenario.minutes
    ]


def _flag_is_on() -> bool:
    """Whether the flag reads on, answering `False` if the provider cannot say.

    The only place in this service where an unreachable provider is not an
    error. This feeds a status line on a page, and a page that fails to render
    because a checkbox could not be filled in is worse than one that renders
    with the checkbox clear.
    """
    try:
        return flags.is_enabled()
    except FlagProviderUnavailable:
        return False
