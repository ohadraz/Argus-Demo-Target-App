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
from target_app.monitoring import AlertNotDelivered, fire_alert
from target_app.scenarios import (
    FALLBACK_FLAG,
    FEATURE_FLAG_TOGGLE,
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
# The second flag guards the safe path, so the shop is well while it is on.
# Its own client rather than a parameter on the first, because a `FlagClient`
# is scoped to one flag by design - two flags are two clients.
fallback_flags = FlagClient(
    settings=get_unleash_settings().model_copy(
        update={"flag": get_unleash_settings().fallback_flag}
    )
)
state = ScenarioState(flags, fallback_flags)


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
    fallback_flags.ensure_flag_exists()
    # Created, deliberately not switched on. On is this flag's healthy state,
    # so turning it on here would look right - and it would be a *flag change*,
    # recorded in the provider's history a few seconds before the first
    # incident. An agent that identifies a culprit by asking which flags
    # recently changed would then find two, refuse to guess between them, and
    # escalate every incident this service stages. The scenario that uses this
    # flag switches it on itself, as the first half of switching it off.
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


class AlertRaised(BaseModel):
    # Whatever the receiver called the incident this alert opened, if it named
    # one at all. `None` rather than an error when it did not: the alert was
    # delivered, and what the other side chose to answer with is its business.
    incident_id: str | None


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
    # The minute somebody put the flag back, once somebody has. Named for the
    # action rather than for the recovery, because that is what it is: this
    # service knows exactly when the flag moved, while when the shop *looked*
    # well again is a judgement about numbers anyone reading them can make for
    # themselves. The two are a minute apart, and conflating them puts a
    # recovery mark on a minute that is still half broken.
    action_at: str | None
    # Which way that action moved the flag. It is not always off: this shop
    # stages incidents in both directions, and the scenario whose fault is a
    # withdrawn kill switch is ended by switching the flag back *on*. A page
    # that assumed one direction would describe half its own scenarios
    # backwards.
    action_enabled: bool | None


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

    *Which* flag is a property of the staged scenario, not of the service: two
    of them stage their incident with the fallback flag rather than the feature
    flag, and reporting the feature flag regardless would show a reader the
    state of something no scenario was touching.
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
            if scenario.offered_in_console
        ],
        active_scenario=state.active_scenario_id,
        flag=_the_staged_flag(),
        flag_is_on=_flag_is_on(),
        phase=state.phase(),
        action_at=_action_at(),
        action_enabled=_action_enabled(),
    )


def _the_staged_flag() -> str:
    """The flag the active scenario stages its incident with.

    Falls back to the feature flag when nothing is staged - it is the shop's
    ordinary flag, and a console with nothing running has to name something.
    """
    settings = get_unleash_settings()
    active = state.active

    if active is not None and active.scenario.flag_role == FALLBACK_FLAG:
        return settings.fallback_flag

    return settings.flag


def _action_enabled() -> bool | None:
    """The state the flag was put into by whoever ended the incident.

    Which is the scenario's healthy state, by definition: ending the incident
    means putting the flag back where the shop is well. For the feature flag
    that is off, for the withdrawn fallback it is on.
    """
    active = state.active

    if active is None or _action_at() is None:
        return None

    return active.scenario.healthy_flag_state


def _action_at() -> str | None:
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


@app.post("/monitoring/alert", response_model=AlertRaised)
def raise_alert() -> AlertRaised:
    """Fires the alert the shop's monitoring would fire, at whatever is
    listening for it (see `target_app.monitoring`).

    Which alert that is comes from the staged scenario rather than from the
    caller: the rule that trips is a property of what is wrong with the service,
    and a console that could choose it would be choosing the incident's
    symptoms.
    """
    try:
        delivered = fire_alert(state.active_scenario_id)
    except AlertNotDelivered as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return AlertRaised(incident_id=delivered.get("incident_id"))


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
    active = state.active
    scenario = active.scenario if active else SCENARIOS[FEATURE_FLAG_TOGGLE]
    settings = get_unleash_settings()

    return generate(
        timeline,
        up_to,
        GENERATED_SPAN_MINUTES,
        flag=(
            settings.fallback_flag
            if scenario.flag_role == FALLBACK_FLAG
            else settings.flag
        ),
        breaks_when_flag_is_on=scenario.breaks_when_flag_is_on,
    )


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
    """Whether the staged scenario's flag reads on, answering `False` if the
    provider cannot say.

    The only place in this service where an unreachable provider is not an
    error. This feeds a status line on a page, and a page that fails to render
    because a checkbox could not be filled in is worse than one that renders
    with the checkbox clear.
    """
    active = state.active
    client = (
        fallback_flags
        if active is not None and active.scenario.flag_role == FALLBACK_FLAG
        else flags
    )

    try:
        return client.is_enabled()
    except FlagProviderUnavailable:
        return False
