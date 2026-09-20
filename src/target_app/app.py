from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from target_app import console
from target_app.flags import FlagClient, FlagProviderUnavailable
from target_app.generator import (
    BASELINE_MEMORY_BYTES,
    MEMORY_LIMIT_BYTES,
    GeneratedMinute,
    generate,
)
from target_app.history import FlagHistoryUnavailable
from target_app.monitoring import AlertNotDelivered, fire_alert
from target_app.oncall import a_user, an_incident
from target_app.people import pay_grades_and_bands
from target_app.payments import charges_between
from target_app.rates import UnknownBase, rates_quoted_against
from target_app.scenarios import (
    FALLBACK_FLAG,
    FEATURE_FLAG,
    FEATURE_FLAG_TOGGLE,
    SCENARIOS,
    TIMESTAMP_FORMAT,
    Scenario,
    bucket_id,
    description_for,
    quiet_state_for,
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
    """Makes sure the shop's flags exist, rest where they belong, and are
    unambiguous, before serving.

    Waits for the provider rather than assuming it: compose ordering already
    holds this container back until the provider is healthy, but a service
    started by hand has no such promise, and crash-looping against a provider
    that is thirty seconds from ready helps nobody.

    Both flags, because the shop has two and the console shows both. Leaving
    the second to be created by the one scenario that stages it meant a
    provider that had never heard of it, and a page reporting an absent flag as
    an off one - the two are indistinguishable through an evaluation endpoint,
    which lists only what is on.
    """
    flags.wait_until_reachable()
    flags.ensure_only_one_environment()

    for client, role in ((flags, FEATURE_FLAG), (fallback_flags, FALLBACK_FLAG)):
        _bring_into_being(client, role)

    yield


def _bring_into_being(client: FlagClient, flag_role: str) -> None:
    """Creates a flag if it is missing, and only then puts it where it rests.

    Only then, and that is the whole care of it. A new flag is created off,
    which is where a feature flag rests but the opposite of where a fallback
    does, so the fallback needs a nudge it cannot be given unconditionally: a
    service restarting in the middle of a staged incident would otherwise
    switch the flag back and end the incident it restarted into.

    Creating a flag records `feature-created`, which is not a toggle and is not
    read as a change by anything watching this provider. The nudge that follows
    *is* a toggle, and it is the reason the environment seeds this flag's row
    directly - see the Target Environment's compose file. Where that seeding
    has run this branch never fires; where it has not, one recorded toggle at
    startup beats a flag sitting in a state the shop's own story says it has
    not been in for months.
    """
    if client.ensure_flag_exists(description_for(flag_role)):
        _set_to(client, quiet_state_for(flag_role))


def _set_to(client: FlagClient, enabled: bool) -> None:
    if enabled:
        client.enable()
    else:
        client.disable()


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


class ShopRestarted(BaseModel):
    # When the process came back. Answered rather than left implicit because it
    # is what the telemetry then reports as the start time, and whoever asked
    # for the restart is about to go looking for exactly that.
    restarted_at: datetime


# The action Argo CD runs against a Deployment to roll it, by the name it is
# registered under. The vendor's own word, so it is named once here rather than
# spelled at the comparison.
RESTART_ACTION = "restart"


class ArgoCdResourceAction(BaseModel):
    """The body Argo CD's resource-action endpoint takes.

    Every field the real one carries, and all of them ignored but `action`.
    This shop has one service and no namespaces, so the resource a caller
    addressed can only be the one there is - but an adapter written against a
    real server sends all five, and a stand-in that refused them would be one
    nothing real could be pointed at.
    """

    action: str
    namespace: str | None = None
    resourceName: str | None = None  # noqa: N815 - Argo CD's own spelling
    group: str | None = None
    kind: str | None = None


class AlertRaised(BaseModel):
    # Whatever the receiver called the incident this alert opened, if it named
    # one at all. `None` rather than an error when it did not: the alert was
    # delivered, and what the other side chose to answer with is its business.
    incident_id: str | None


class ScenarioFlag(BaseModel):
    """A flag a scenario puts in play, and what its position means there.

    `breaks_when_on` is the position in which this flag breaks the shop, and it
    is `None` for a flag that breaks it in neither - a decoy, or the flag in the
    scenario where a real toggle turns out to be a coincidence. That is not a
    detail: a flag with no breaking position is the whole point of those
    scenarios, and a page that gave every flag in play a guilty position would
    be showing an audience an incident with no ambiguity in it.

    Meaning is per scenario rather than per flag, because it is. The same flag
    is the fault in one scenario and a bystander in the next.
    """

    name: str
    breaks_when_on: bool | None


class ScenarioSummary(BaseModel):
    id: str
    title: str
    description: str
    is_generated: bool
    # Only the flags this scenario puts in play. The shop has two, and most
    # scenarios use one - a badge for a flag the selected scenario never touches
    # invites a reader to watch something that is not going to move.
    flags: list[ScenarioFlag]


class FlagState(BaseModel):
    """One of the shop's flags, and what it currently reads.

    `None` when the provider could not be reached. Not `False`: an unreachable
    provider and a flag that is off are opposite facts, and a page that reports
    the first as the second draws a flag as sitting somewhere it may not be -
    which, for the one flag whose off position breaks the shop, is an incident
    invented out of an outage.
    """

    name: str
    is_on: bool | None


class ActionMoment(BaseModel):
    """One flag change somebody made after the incident was staged.

    `at` is when the flag moved, which this service knows exactly - not when
    the shop *looked* well again, which is a judgement about numbers anyone
    reading them can make for themselves. The two are a minute apart, and
    conflating them puts a recovery mark on a minute that is still half broken.

    `enabled` is which way it moved, and it is not always off: this shop stages
    incidents in both directions, and the one whose fault is a withdrawn kill
    switch is ended by switching a flag back *on*. A page that assumed one
    direction would describe half its own scenarios backwards.
    """

    at: str
    flag: str
    enabled: bool


class ScenarioCatalog(BaseModel):
    scenarios: list[ScenarioSummary]
    active_scenario: str | None
    # Every flag the shop has, not only the staged scenario's own. A scenario
    # can move two of them - one that matters and one that does not - and a
    # reader watching a single badge would see half of what an agent is
    # reacting to, which is the half that makes the incident ambiguous.
    flags: list[FlagState]
    phase: str
    # Every action taken since staging, in order. A list rather than one
    # moment, because being wrong once is the ordinary case: an agent reverts
    # the flag it suspects, finds the shop still broken, puts it back and tries
    # another. Those three moments are the story, and showing only the last of
    # them would show an audience a lucky guess.
    actions: list[ActionMoment]


class MetricBucket(BaseModel):
    bucket_id: str
    error_rate: float
    p50_ms: int
    p95_ms: int
    request_volume: int
    # The resource fields, reported by every bucket of every scenario. Gauges
    # where the four above are rates and quantiles, so each minute takes the
    # peak for usage and the last reading for the other two - a minute
    # containing a restart reports the process that finished it.
    memory_used_bytes: int
    memory_limit_bytes: int | None = None
    process_start_time_seconds: float


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
                flags=_the_flags_in_play_for(scenario),
            )
            for scenario in SCENARIOS.values()
            if scenario.offered_in_console
        ],
        active_scenario=state.active_scenario_id,
        flags=_the_shops_flags(),
        phase=state.phase(),
        actions=_the_actions_taken(),
    )


def _the_shops_flags() -> list[FlagState]:
    """Both of the shop's flags and what each reads, whatever is staged.

    Always both, and always in the same order, so a badge does not move about
    the page as scenarios come and go. An unreachable provider reports `False`
    rather than failing: this feeds a status line, and a page that will not
    render because a checkbox could not be filled in is worse than one that
    renders with the checkbox clear.
    """
    return [_the_state_of(client) for client in (flags, fallback_flags)]


def _the_flags_in_play_for(scenario: Scenario) -> list[ScenarioFlag]:
    """The flags a scenario moves, and where each one breaks the shop.

    An authored scenario moves none: its telemetry is a fixed list of minutes
    with no live condition behind it, and offering a flag to watch would be
    offering a control that does nothing. Nor do the two generated scenarios
    whose condition is not a flag's value - a heap that climbs and a provider
    that stopped answering - and for a sharper reason: a flag named beside
    either would be a suspect the fixture invented.

    A breaking position is claimed only where reverting the flag really does
    end the incident. Two scenarios stage a flag that moved and did not matter
    - a decoy, and a toggle that turns out to be a coincidence - and in both,
    no position of that flag breaks anything. That is what makes them the cases
    an agent has to be *wrong* about, and a page that painted every flag in play
    as guilty would quietly delete the difference.

    Ordered by the shop's own flags rather than by role, so the culprit is not
    given away by which badge comes first.
    """
    if not scenario.stages_a_flag:
        return []

    breaking_position = {
        scenario.flag_role: (
            scenario.breaks_when_flag_is_on
            if scenario.recovers_when_flag_reverts
            else None
        )
    }
    if scenario.decoy_flag_role is not None:
        breaking_position[scenario.decoy_flag_role] = None

    return [
        ScenarioFlag(name=name, breaks_when_on=breaking_position[role])
        for role, name in ((FEATURE_FLAG, flags.name), (FALLBACK_FLAG, fallback_flags.name))
        if role in breaking_position
    ]


def _the_state_of(client: FlagClient) -> FlagState:
    try:
        return FlagState(name=client.name, is_on=client.is_enabled())
    except FlagProviderUnavailable:
        return FlagState(name=client.name, is_on=None)


def _the_actions_taken() -> list[ActionMoment]:
    """Every flag change made since staging, oldest first.

    Read from the provider on the way past, which is the only way this service
    finds anything out about flags: nobody announces a change to it, and the
    party making them is usually not this service at all.
    """
    return [
        ActionMoment(
            at=moment.at.replace(second=0, microsecond=0).strftime(TIMESTAMP_FORMAT),
            flag=moment.flag,
            enabled=moment.enabled,
        )
        for moment in state.observe_the_flags()
    ]


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
    except (FlagProviderUnavailable, FlagHistoryUnavailable) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ScenarioStatus(active_scenario=state.active_scenario_id)


@app.post("/scenario/restart", response_model=ShopRestarted)
def restart_the_shop() -> ShopRestarted:
    """Brings the shop's serving process back.

    Under the scenario prefix because it is a control on the fixture rather
    than something the shop offers its shoppers - the same place the seed and
    the reset live.

    It is not, however, the only way in. A platform restart arrives at the Argo
    CD endpoint below, and both land here, because a mitigation that behaved
    differently depending on who asked for it would be a fixture grading itself.
    """
    return ShopRestarted(restarted_at=state.restart_the_shop())


@app.post("/argocd/{application}/resource/actions/v2")
def argocd_run_resource_action(application: str,
                               body: ArgoCdResourceAction) -> dict[str, str]:
    """Stands in for Argo CD's `POST
    /api/v1/applications/{name}/resource/actions/v2`.

    A restart on a real platform is not an endpoint of its own: it is a named
    action the server runs against a resource, and `restart` is the built-in one
    for a Deployment. The shape here is the vendor's for the reason every other
    stand-in in this file uses the vendor's - the adapter pointed at this is the
    same adapter that would be pointed at a real Argo CD.

    Anything other than the restart action is refused rather than quietly
    accepted. A platform that answered 200 to an action it did not run would
    have a caller believe production had changed when it had not.

    Argo CD answers an empty body on success, and so does this.
    """
    if body.action != RESTART_ACTION:
        raise HTTPException(
            status_code=400,
            detail=f"unknown resource action: {body.action}",
        )

    state.restart_the_shop()

    return {}


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

    # After it was delivered, because an alert nobody received paged nobody -
    # and the on-call provider counts every responder's minutes from this.
    state.somebody_was_paged()

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
                memory_used_bytes=minute.memory_used_bytes,
                memory_limit_bytes=minute.memory_limit_bytes,
                process_start_time_seconds=minute.process_start_time_seconds,
            )
            for minute in _generated_minutes()
        ]

    return _authored_metrics(
        active.scenario, active.seeded_at, active.process_started_at
    )


# Stripe's own list envelope, field names included - `object`, `has_more`, and
# a `url` naming the resource. Deliberately not this service's house style, for
# the reason the Argo CD models above give: the adapter reading this is the
# same code that reads the real provider.
class StripeList(BaseModel):
    object: str = "list"
    data: list[dict]
    has_more: bool
    url: str = "/v1/charges"


@app.get("/stripe/v1/charges", response_model=StripeList)
def stripe_charges(
    created_gte: int = Query(0, alias="created[gte]"),
    created_lte: int = Query(0, alias="created[lte]"),
    limit: int = Query(100),
    starting_after: str | None = Query(None),
) -> StripeList:
    """Stands in for Stripe's `GET /v1/charges`.

    The window arrives as the SDK sends it - `created[gte]` and `created[lte]`,
    unix seconds - and paging works as the SDK expects, because the client
    pages to the end of a window and a stand-in that answered everything in one
    page would leave that path untested until a real account was in front of
    it.

    Takings come from the same minutes `/metrics` reports, so an incident that
    breaks the shop shows up in the money. A window with no scenario seeded is
    a shop that took nothing, which is a real answer and not an error.
    """
    window_start = datetime.fromtimestamp(created_gte, tz=UTC)
    window_end = datetime.fromtimestamp(created_lte or created_gte, tz=UTC)

    charges = charges_between(metrics(), window_start, window_end)

    if starting_after is not None:
        seen = [index for index, charge in enumerate(charges)
                if charge["id"] == starting_after]
        charges = charges[seen[0] + 1:] if seen else []

    page = charges[:limit]

    return StripeList(data=page, has_more=len(charges) > len(page))


# PagerDuty's own envelope: a single resource comes back wrapped under its own
# name, and the SDK unwraps it. Answering the bare object would work against a
# hand-built request and fail against the client a real account uses.
@app.get("/pagerduty/incidents/{incident_id}")
def pagerduty_incident(incident_id: str) -> dict[str, Any]:
    """Stands in for PagerDuty's `GET /incidents/{id}`.

    Whatever incident id is asked for is answered from the scenario that is
    seeded, because in this demo there is one incident at a time and Argus's
    own id for it is the only one it has. A real account would need a mapping
    between the two, which is a deployment's problem and not a fixture's.

    With no scenario seeded - or one nobody has alerted on - there is no
    incident to have been paged for, and saying so as a 404 is what the SDK
    turns into the error the adapter already answers "could not say" to.
    """
    active = state.active
    incident = an_incident(
        incident_id,
        metrics(),
        active.alerted_at if active is not None else None
    )

    if incident is None:
        raise HTTPException(status_code=404, detail="no incident is running")

    return {"incident": incident}


@app.get("/bamboohr/api/v1/pay-grades-and-bands/job-titles")
def bamboohr_pay_grades_and_bands() -> dict[str, Any]:
    """Stands in for BambooHR's `GET /pay-grades-and-bands/job-titles`.

    No parameters and no filtering, exactly as the real endpoint has none: it
    answers levels-with-titles, and a caller wanting one title's band inverts
    what comes back. Nothing here is scenario-dependent - what a title is worth
    is a fact about the shop, not about the incident it is having.
    """
    return pay_grades_and_bands()


@app.get("/frankfurter/v1/latest")
def frankfurter_latest(base: str = Query("EUR")) -> dict[str, Any]:
    """Stands in for Frankfurter's `GET /v1/latest`.

    Defaults to the euro because the provider does: the table is the ECB's, and
    a caller that names no base gets it in the currency it was published in.

    A base nothing is quoted against is a 404, as it is there - the one failure
    of this endpoint a consumer can provoke, and the one its "the rates could
    not be read" path is waiting for.
    """
    try:
        return rates_quoted_against(base)
    except UnknownBase as unknown:
        raise HTTPException(status_code=404, detail=str(unknown)) from unknown


@app.get("/pagerduty/users/{user_id}")
def pagerduty_user(user_id: str) -> dict[str, Any]:
    """Stands in for PagerDuty's `GET /users/{id}`.

    The job title lives here rather than on the acknowledgement, which is what
    makes reading it a second request - and a user nobody holds is a 404, so
    the adapter's own "then the title is simply unknown" path is exercised by
    the demo rather than only by a unit test.
    """
    user = a_user(user_id)

    if user is None:
        raise HTTPException(status_code=404, detail="no such user")

    return {"user": user}


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
        decoy_flag=_the_decoy_flag(scenario),
        decoy_timeline=state.decoy_timeline_now(),
        process_started_at=active.process_started_at if active else None,
        leak_started_at=active.leak_started_at if active else None,
        restarts=active.restarts if active else (),
        provider_outage=active.provider_outage if active else None,
    )


def _the_decoy_flag(scenario: Scenario) -> str | None:
    """The name of the second flag this scenario moved, if it moved one."""
    if scenario.decoy_flag_role is None:
        return None

    settings = get_unleash_settings()

    return (
        settings.fallback_flag
        if scenario.decoy_flag_role == FALLBACK_FLAG
        else settings.flag
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


def _authored_metrics(scenario: Scenario,
                      seeded_at: datetime | None,
                      process_started_at: datetime | None) -> list[MetricBucket]:
    """An authored scenario's minutes as buckets, resources included.

    The resources are the calm baseline, flat across the window: an authored
    scenario is a fault in something other than memory, and a fixture that
    moved every metric at once would leave a reader unable to tell which one
    the incident is about.
    """
    if seeded_at is None or process_started_at is None:
        return []

    span_minutes = scenario_span_minutes(scenario)
    return [
        MetricBucket(
            bucket_id=bucket_id(seeded_at, entry.offset_minutes, span_minutes),
            error_rate=entry.error_rate,
            p50_ms=entry.p50_ms,
            p95_ms=entry.p95_ms,
            request_volume=entry.request_volume,
            memory_used_bytes=BASELINE_MEMORY_BYTES,
            memory_limit_bytes=MEMORY_LIMIT_BYTES,
            process_start_time_seconds=process_started_at.timestamp(),
        )
        for entry in scenario.minutes
    ]


