from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from io_shop.endpoints import the_shops_routes
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from target_app import alert_rules, console, prometheus
from target_app.accelerators import (
    GPU_PRODUCT_LABEL,
    PRICING_NODE,
    ReplicaPlacement,
)
from target_app.cache_entries import the_key_for
from target_app.flags import FlagClient, FlagProviderUnavailable
from target_app.generator import (
    BASELINE_MEMORY_BYTES,
    MEMORY_LIMIT_BYTES,
    REPLICAS_DURING_A_ROLLOUT,
    REPLICAS_ON_THE_NEWER_SIDE,
    SETTLED_UPTIME,
    FlagTimeline,
    GeneratedMinute,
    PausedRollout,
    SlowRollout,
    generate,
    the_cores_of,
    the_cpu_demanded_by,
)
from target_app.history import FlagHistoryUnavailable
from target_app.monitoring import (
    FINDING_RULE_UIDS,
    AlertNotDelivered,
    an_alert_for,
    fire_alert,
    the_rule_for,
    the_rule_linked_from,
)
from target_app.oncall import a_user, an_incident
from target_app.payments import a_page_of_charges
from target_app.people import pay_grades_and_bands
from target_app.rates import UnknownBase, rates_quoted_against
from target_app.registry import dependencies_of
from target_app.relapse import with_relapses
from target_app.scenarios import (
    FALLBACK_FLAG,
    FAMILY_ORDER,
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
from target_app.settings import (
    get_prometheus_settings,
    get_scenario_settings,
    get_unleash_settings,
    the_deployed_replica_count,
)
from target_app.single_flight import SingleFlight
from target_app.state import ScenarioState

# How much history the generated channels serve. Six hours, because that is
# the window a responder actually asks for: Argus fetches its metrics summary
# over a fixed six-hour span anchored on the alert, and a shop serving ninety
# minutes answered a quarter of it.
#
# The quarter that was missing was not missing data a reader could notice - it
# simply was not there to ask for. So every prompt measured against this
# fixture was a quarter the size of the one production would send, and any
# bound calibrated from those measurements was a quarter of the number it
# needed to be.
#
# Cost is not what decides this figure, and used to be. A finished minute is
# generated once and remembered (`generator._a_whole_minute`), so a longer
# window costs one pass rather than one per request - which is what let this
# become a question about what a window means instead of what it costs.
GENERATED_SPAN_MINUTES = 360

# How long before the change that broke it the previous revision went out. Far
# enough back to be outside the incident and plainly not its cause, close
# enough to be in a history a responder is looking at.
_A_PREVIOUS_DEPLOY_AGO = timedelta(hours=4)

# What stops forty readers each rebuilding the same window - see
# `_generated_minutes`. Keyed on the active scenario, so a reset that swaps one
# for another is never served a window the scenario before it was building.
_the_window_being_built: SingleFlight[list[GeneratedMinute]] = SingleFlight()


def to_bucket_id(moment: datetime) -> str:
    """One instant as the minute id every other channel spells it with."""
    return moment.replace(second=0, microsecond=0).strftime(TIMESTAMP_FORMAT)

# What the platform calls the pricing service. The one application name this
# file reads rather than echoing back, because restarting the wrong process is
# the mistake this scenario is built to catch - see
# `argocd_run_resource_action`.
PRICING_APPLICATION = "io-pricing"

# What the rest of a pod's name looks like. A platform's pods carry a suffix from
# the replica set that made them, and a caller reading one only ever reads that
# it changed - so any fixed spelling does, and a random one would make a pod
# appear to have been replaced on every poll.
_A_POD_SUFFIX = "7d9c4f8b6-x2k9p"
# The replica set's half of every replica's name, and the per-pod halves that
# tell them apart. The first replica keeps the name a one-pod tree always gave
# it; the rest are spelled the way a replica set spells them, and the index is
# the fallback for a deployment scaled past what is spelled out here.
_THE_REPLICA_SETS_HASH = "7d9c4f8b6"
_THE_PODS_OWN_SUFFIXES = (
    "x2k9p", "m4q7r", "c8v2n", "h5t3w", "b9f6k", "r2d8s",
    "w7n4j", "p3g5z", "k6y2m", "t9c4x", "f8w3q", "n5r7v",
)
# The info item Argo CD puts on a Pod naming the node it runs on.
NODE_INFO_ITEM = "Node"

# Argo CD's name for an RFC 7386 merge patch, as `ApplicationPatchRequest`
# spells its `patchType`.
_A_MERGE_PATCH = "merge"

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
    # The instant the scenario was put in place, which is the instant its whole
    # window hangs off: the minutes are numbered back from it and the deploy
    # history is dated against it. Reported because a consumer replaying a
    # recorded walk has to line that walk's frozen timestamps up with the world
    # in front of it, and the only honest anchor is the one this service used.
    #
    # `None` where nothing is staged, which is the same answer as an absent
    # `active_scenario` and for the same reason: a shop with no scenario has no
    # seeding to date anything from.
    seeded_at: datetime | None = None
    # The uid of the series rule a page about the scenario names - what Grafana's
    # webhook names it by: the rule the scenario trips, or the shop's default
    # error-rate rule for one whose own page is a finding rather than a series.
    # Reported because whoever stages an incident and pages about it by hand has
    # to say which rule fired. `None` where nothing is staged, for the reason
    # `seeded_at` is.
    rule_uid: str | None = None


class ShopRestarted(BaseModel):
    # When the process came back. Answered rather than left implicit because it
    # is what the telemetry then reports as the start time, and whoever asked
    # for the restart is about to go looking for exactly that.
    restarted_at: datetime


# The action Argo CD runs against a Deployment to roll it, by the name it is
# registered under. The vendor's own word, so it is named once here rather than
# spelled at the comparison.
RESTART_ACTION = "restart"
# And the one that resizes it. Also the vendor's - Argo CD ships both as built-in
# actions for `apps/Deployment`, which is why neither is an endpoint of its own.
SCALE_ACTION = "scale"
# The parameter that action reads, by the name its own script reads it under. A
# string on the wire, because every resource-action parameter is: the value is
# carried as text and the action's script is what makes a number of it.
REPLICAS_PARAMETER = "replicas"

# How the platform addresses the autoscaler, in Kubernetes' own vocabulary. The
# resource endpoint dispatches on these, which is what lets one route answer for
# two kinds - and `autoscaling/v2` is the version that carries `behavior`, so a
# stand-in reporting `v1` would be reporting a resource in which this deployment's
# fault cannot be expressed.
AUTOSCALER_GROUP = "autoscaling"
AUTOSCALER_VERSION = "v2"
AUTOSCALER_KIND = "HorizontalPodAutoscaler"
# What the autoscaler is called, as a suffix on the application's name. Deliberately
# not the application's own name: Kubernetes does not require an autoscaler to share
# its target's, so a fixture that made them equal would let a caller address the
# application and pass, and the one that addressed the resource correctly would look
# no different.
_THE_AUTOSCALERS_SUFFIX = "-cpu"

# What a caller may patch, and the type of patch that carries it. A merge patch is
# Argo CD's own default and the only one accepted here: a JSON patch is a list of
# operations rather than a document, and a stand-in that read one shape and
# claimed the other would be teaching a caller a wire that does not exist.
MERGE_PATCH_TYPE = "application/merge-patch+json"
SPEC_FIELD = "spec"
MIN_REPLICAS_FIELD = "minReplicas"
# And the one field of the Deployment a caller may patch: the node selector on
# its pod template, and on that only the GPU product label. Kubernetes' own
# names, all the way down.
DEPLOYMENT_KIND = "Deployment"
TEMPLATE_FIELD = "template"
NODE_SELECTOR_FIELD = "nodeSelector"

# How a Deployment says how far a rolling update has got, in Kubernetes' own
# vocabulary. A rollout in progress and a rollout that finished are the same
# condition at different statuses, which is why the reason is what a reader
# dispatches on: `Progressing`/`Unknown`/`DeploymentPaused` is a deployment
# holding where it was stopped, and `Progressing`/`True`/`NewReplicaSetAvailable`
# is one that converged. Spelled out because a stand-in inventing its own words
# for this would be teaching a caller a wire that does not exist.
ROLLING_UPDATE_STRATEGY = "RollingUpdate"
PROGRESSING_CONDITION = "Progressing"
ROLLOUT_PAUSED_REASON = "DeploymentPaused"
ROLLOUT_COMPLETE_REASON = "NewReplicaSetAvailable"
_PROGRESSING = "True"
_PROGRESS_UNKNOWN = "Unknown"


class ArgoCdActionParameter(BaseModel):
    """One argument to a resource action, in Argo CD's shape for it.

    A name and a value, both strings, which is the whole of the vendor's message
    - so a caller that sends a count sends it as text, and whoever runs the
    action is what turns it into a number.
    """

    name: str
    value: str


class ArgoCdResourceAction(BaseModel):
    """The body Argo CD's resource-action endpoint takes.

    Every field the real one carries, and all of them ignored but `action` and
    the parameters. This shop has one service and no namespaces, so the resource
    a caller addressed can only be the one there is - but an adapter written
    against a real server sends all five, and a stand-in that refused them would
    be one nothing real could be pointed at.

    `resourceActionParameters` is what separates this endpoint from the v1 it
    replaced, and it is why a scale can be asked for at all: a restart needs
    nothing said about it, where a resize is a number somebody has to name.
    """

    action: str
    namespace: str | None = None
    resourceName: str | None = None
    group: str | None = None
    kind: str | None = None
    # Argo CD's own spelling, and its own shape - a list of named values rather
    # than an object, because an action declares its parameters in order.
    resourceActionParameters: list[ArgoCdActionParameter] = Field(
        default_factory=list
    )


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
    # Which group this belongs under, by id. The family's own name and text are
    # sent once in `families` rather than repeated on every scenario that shares
    # it - three scenarios carrying three copies of the same paragraph is three
    # chances for a page to draw a heading that disagrees with itself.
    family: str
    # Only the flags this scenario puts in play. The shop has two, and most
    # scenarios use one - a badge for a flag the selected scenario never touches
    # invites a reader to watch something that is not going to move.
    flags: list[ScenarioFlag]


class ScenarioFamilySummary(BaseModel):
    """One group of scenarios, as a console draws it.

    Sent as a list rather than left for a page to derive from the scenarios,
    because two of the three things a group needs are not derivable: the order
    the groups go in, and what a group would say about itself. Deriving the
    order from the scenarios would put the families in whichever sequence the
    catalogue happens to be written in.
    """

    id: str
    name: str
    taxonomy: str
    blurb: str


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
    # The groups the scenarios above fall into, in the order to draw them, and
    # only the ones something offered actually falls into. An empty group would
    # be a claim the shop cannot stage that kind of incident at all - true, and
    # not this page's news: the console is where somebody picks what to run, and
    # a family with nothing under it is a dead end in the one control they came
    # for. What Argus does and does not cover is argued in the backlog.
    families: list[ScenarioFamilySummary]
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
    # The slowest one request in a hundred. Reported on every bucket of every
    # scenario, and required rather than optional: no deployment lacks a tail,
    # so an absent one would be a measurement that went missing rather than a
    # fact about the service - which is what separates it from the hit ratio
    # below, where absence says something a zero could not.
    p99_ms: int
    request_volume: int
    # The resource fields, reported by every bucket of every scenario. Gauges
    # where the four above are rates and quantiles, so each minute takes the
    # peak for usage and the last reading for the other two - a minute
    # containing a restart reports the process that finished it.
    memory_used_bytes: int
    memory_limit_bytes: int | None = None
    process_start_time_seconds: float
    # What the shop's replicas were using, and what they had between them. A pair
    # like memory's rather than a utilisation ratio, for the same reason: the ratio
    # is derivable from the pair and the pair is not derivable from the ratio.
    #
    # Capacity is the deployment's total across its replicas, so scaling it moves
    # the denominator and the recovery is visible in the series the incident was.
    # The mean of the minute rather than its peak, which is where this gauge parts
    # company with memory: a heap's peak is what breaches a limit, where a second
    # at full CPU is what an ordinary busy minute contains.
    cpu_used_cores: float
    cpu_limit_cores: float | None = None
    # How much of the minute's work the summary cache carried. A rate over the
    # minute like the error rate, not a gauge - averaging ratios taken over
    # unequal numbers of lookups would weight a quiet instant as heavily as a
    # busy one. Absent, rather than zero, for a deployment with no cache
    # configured: zero is what a cache answering nothing reports, and a reader
    # has to be able to tell a service without a cache from one whose cache
    # has gone.
    cache_hit_ratio: float | None = None
    # How much of what was bought the categoriser filed with confidence. Present
    # on every generated minute, because the shop always files its purchases;
    # absent from an authored one, which carries no purchases to file.
    categoriser_confident_ratio: float | None = None
    # How much of what was bought the fraud scorer held for review. Present on
    # every generated minute for the reason the categoriser's share is, and
    # absent from an authored one for the same reason.
    fraud_held_for_review_ratio: float | None = None


# The four models below mirror Argo CD's own wire shape, field names included -
# `repoURL`, `deployedAt`, `targetRevision`. They are deliberately camelCase and
# deliberately not this service's house style: the point of the stand-in is that
# the adapter reading it is the same code that reads a real Argo CD server,
# so anything renamed here would be a lie the adapter would have to be written
# around.
class ArgoCdSource(BaseModel):
    repoURL: str
    path: str
    targetRevision: str


class ArgoCdInitiator(BaseModel):
    username: str


class ArgoCdRevisionHistory(BaseModel):
    id: int
    revision: str
    deployedAt: str
    deployStartedAt: str
    source: ArgoCdSource
    initiatedBy: ArgoCdInitiator


class ArgoCdApplicationMetadata(BaseModel):
    name: str
    namespace: str


class ArgoCdApplicationStatus(BaseModel):
    history: list[ArgoCdRevisionHistory]


class ArgoCdAutomatedSync(BaseModel):
    """The automated half of a sync policy.

    Argo CD spells the presence of this object as "this application syncs
    itself", unless `enabled` is `false` - the switch Argo CD 3.1 added so that
    automated sync can be turned off while what it was configured to do is kept.
    `enabled` absent means on. An application with no `automated` key is one a
    human syncs, which is why the field above it is optional rather than
    defaulting to anything.

    Unknown fields are refused rather than dropped: a patch that set one would
    otherwise be answered as accepted and change nothing.
    """

    model_config = ConfigDict(extra="forbid")

    prune: bool = False
    selfHeal: bool = False
    enabled: bool | None = None


class ArgoCdSyncPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automated: ArgoCdAutomatedSync | None = None


class ArgoCdApplicationPatch(BaseModel):
    """The body of Argo CD's `PATCH /api/v1/applications/{name}` - its
    `ApplicationPatchRequest`, bound with `body: "*"`. `patch` is the patch
    itself as a JSON string, applied to the whole application.
    """

    name: str
    patch: str
    patchType: str


class ArgoCdApplicationSpec(BaseModel):
    syncPolicy: ArgoCdSyncPolicy = ArgoCdSyncPolicy()


class ArgoCdApplication(BaseModel):
    metadata: ArgoCdApplicationMetadata
    # Before `status`, as Argo CD orders them: the spec is what was asked for
    # and the status is what happened. A rollback reads both - it cannot run
    # while the spec says the application syncs itself, because the next
    # reconciliation would undo it.
    spec: ArgoCdApplicationSpec = ArgoCdApplicationSpec()
    status: ArgoCdApplicationStatus


class RegisteredDependency(BaseModel):
    """One dependency as the registry serves it.

    A model of its own rather than the dataclass returned directly, for the
    reason every other response here has one: what crosses the wire is a
    published shape, and letting an internal value class be that shape makes
    every rename of a field a change to somebody else's parser.
    """

    name: str
    purpose: str
    host: str
    owner: str
    ownership: str


class RegisteredServiceResponse(BaseModel):
    service: str
    dependencies: list[RegisteredDependency]


class ArgoCdInfoItem(BaseModel):
    name: str
    value: str


class ArgoCdResourceNode(BaseModel):
    """One live object the platform sees under an application.

    Argo CD's resource tree is how anybody finds out what is actually running:
    the pod's `createdAt` is when that process came up, and it is the platform's
    own answer to "did the restart land". A monitoring stack's process start time
    says the same thing from the other side, and both exist here for the reason
    both exist in real life - a platform is asked about pods and a monitor is
    asked about series.

    Only the fields a caller confirming a restart reads, plus the two a caller
    *finding* a resource needs. A real node carries its health, its parents and
    its resource version too, and a stand-in that invented values for those would
    be putting figures into the world for nobody to read.

    `group` and `version` are those two, and they are here because the tree is now
    read for more than one kind: a pin looks through it for the autoscaler, and
    what identifies a resource to Argo CD is the triple of group, version and
    kind rather than the kind alone. Absent on a core resource, which is the
    vendor's own shape - a Pod's group is the empty one, and reporting `""` for it
    would be a spelling a caller has to know to compare against.
    """

    kind: str
    name: str
    group: str | None = None
    version: str | None = None
    namespace: str
    createdAt: str
    # What the platform says about the object beyond its identity, as name and
    # value pairs. A Pod's carries the node it runs on, under `Node`; nothing
    # else here carries any.
    info: list[ArgoCdInfoItem] | None = None


class ArgoCdHost(BaseModel):
    """One node the application's pods run on, as the tree reports it.

    Argo CD lists the nodes under `hosts` with whatever labels its configuration
    allow-lists (`application.allowedNodeLabels`), and nothing else of the
    node's labels. This one is configured to carry the GPU product, which is how
    a caller learns what card a pod is running on without a cluster credential.
    `resourcesInfo` and `systemInfo` are left out for the reason a node's health
    is: nothing reads them.
    """

    name: str
    labels: dict[str, str]


class ArgoCdResourceTree(BaseModel):
    nodes: list[ArgoCdResourceNode]
    hosts: list[ArgoCdHost] = []


class ArgoCdManagedResource(BaseModel):
    """One live object's manifest, as the platform is holding it.

    A string and not an object, because that is Argo CD's own shape: the manifest
    is passed through as text for the caller to parse. What a caller comes here
    for is `spec.replicas` - how many replicas are actually running, which the
    repository cannot answer once anybody has scaled the deployment.
    """

    manifest: str


class ArgoCdRollback(BaseModel):
    """The body Argo CD's rollback endpoint takes.

    `id` is the history entry to return to - the same integer the application's
    revision history reports, which is how a caller names a revision that was
    actually deployed rather than any commit it happens to know about.
    """

    name: str | None = None
    id: int
    prune: bool = False
    dryRun: bool = False


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
    offered = [
        scenario for scenario in SCENARIOS.values() if scenario.offered_in_console
    ]

    return ScenarioCatalog(
        scenarios=[
            ScenarioSummary(
                id=scenario.id,
                title=scenario.title,
                description=scenario.description,
                is_generated=scenario.is_generated,
                family=scenario.family.id,
                flags=_the_flags_in_play_for(scenario),
            )
            for scenario in offered
        ],
        families=_the_families_offered(offered),
        active_scenario=state.active_scenario_id,
        flags=_the_shops_flags(),
        phase=state.phase(),
        actions=_the_actions_taken(),
    )


def _the_families_offered(offered: list[Scenario]) -> list[ScenarioFamilySummary]:
    """The groups these scenarios fall into, in the taxonomy's own order.

    `FAMILY_ORDER` is what fixes the sequence, and iterating it rather than the
    scenarios is the whole point: the order is a property of the families - the
    share of real incidents each accounts for - and reading it off whichever
    scenario came first in the catalogue would let adding a scenario silently
    reorder the panel it appears in.
    """
    holds_something = {scenario.family.id for scenario in offered}

    return [
        ScenarioFamilySummary(
            id=family.id,
            name=family.name,
            taxonomy=family.taxonomy,
            blurb=family.blurb,
        )
        for family in FAMILY_ORDER
        if family.id in holds_something
    ]


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

    return _the_scenario_now()


@app.post("/scenario/reset", response_model=ScenarioStatus)
def reset_scenario() -> ScenarioStatus:
    try:
        state.reset()
    except (FlagProviderUnavailable, FlagHistoryUnavailable) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return _the_scenario_now()


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


def _the_platform_has_to_answer() -> None:
    """Refuses an action where the staged platform is carrying none.

    `503` rather than a hang. A hang is the more literal unreachability and the
    wrong one to stage: a caller waits its whole timeout for one, on a walk that
    reaches for the platform more than once, and learns at the end of it exactly
    what it learns here at once. It is also what a downed API server behind an
    ingress actually answers.

    Called by the routes that act and by none of the routes that report. A
    platform that hid its own history would take the deployment out of the
    change channel a reader diagnoses from, and the incident would be about not
    seeing rather than about not acting.
    """
    if state.the_platform_will_not_act:
        raise HTTPException(
            status_code=503,
            detail="the deployment platform's API server is unavailable"
        )


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

    Two actions are run here, and both are the vendor's built-ins for a
    Deployment: `restart`, and `scale`, which reads a replica count from the
    action's parameters. Anything else is refused rather than quietly accepted. A
    platform that answered 200 to an action it did not run would have a caller
    believe production had changed when it had not - which is also why a count
    that is not a number is refused rather than rounded, ignored or defaulted.

    Which application was addressed is read here, and it is the one place in this
    file where that matters. Everything else answers from the staged scenario
    whatever name it is asked about, because there is one shop and one window;
    a restart is different because two processes can be restarted and only one of
    them is the right one. A stand-in that restarted the shop whoever was named
    would grade every mitigation as correct.

    An application nobody recognises restarts the shop, which is the same
    permissiveness the rest of this file has: the fixture knows two applications,
    and refusing a third would be inventing an estate for a caller to get wrong.

    Argo CD answers an empty body on success, and so does this.
    """
    _the_platform_has_to_answer()

    if body.action == SCALE_ACTION:
        _scale(application, body)

        return {}

    if body.action != RESTART_ACTION:
        raise HTTPException(
            status_code=400,
            detail=f"unknown resource action: {body.action}",
        )

    if application == PRICING_APPLICATION:
        state.restart_the_pricing_service()
    else:
        state.restart_the_shop()

    return {}


def _scale(application: str, body: ArgoCdResourceAction) -> None:
    """Resizes the addressed deployment, or says why it did not.

    The count is the shop's alone, so the pricing service is refused rather than
    silently resized: the fixture holds no size for it, and a 200 would have a
    caller believe a neighbour grew. This is the one place this file is stricter
    about the application than the restart above, and the reason is the reverse of
    that one's - a restart the fixture can perform for either process is
    permissive because both are real, where a size only one of them has cannot be
    invented for the other.
    """
    if application == PRICING_APPLICATION:
        raise HTTPException(
            status_code=400,
            detail=f"{PRICING_APPLICATION} has no replica count to set",
        )

    state.scale_the_deployment_to(_the_replica_count_in(body))


def _the_replica_count_in(body: ArgoCdResourceAction) -> int:
    """The count the action was asked for, or a refusal.

    Refused three ways, all of them 400, because all three are the same mistake
    said differently: the parameter absent, its value not a number, and a number
    that is not a count. Argo CD's own script errors on the middle one and the
    server reports that as a failed action, which is what a caller has to be able
    to tell from a size it successfully set.
    """
    for parameter in body.resourceActionParameters:
        if parameter.name != REPLICAS_PARAMETER:
            continue

        try:
            replicas = int(parameter.value)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"invalid number: {parameter.value}",
            ) from None

        if replicas < 1:
            raise HTTPException(
                status_code=400,
                detail=f"replicas must be at least one, not {replicas}",
            )

        return replicas

    raise HTTPException(
        status_code=400,
        detail=f"the {SCALE_ACTION} action requires a {REPLICAS_PARAMETER} parameter",
    )


@app.get("/argocd/{application}/resource-tree", response_model=ArgoCdResourceTree)
def argocd_resource_tree(application: str) -> ArgoCdResourceTree:
    """Stands in for Argo CD's `GET
    /api/v1/applications/{name}/resource-tree`.

    One pod per replica, and each pod's `createdAt` is when that process came up.
    This is what confirms a restart landed, and it is per application on purpose:
    restarting the pricing service moves its pod's creation time and leaves the
    shop's where it was, which is the only evidence distinguishing "the
    dependency was restarted" from "something was restarted".

    Each pod names the node it runs on, and `hosts` lists those nodes with the
    one label this platform is configured to report - the GPU product. That is
    the only place a caller can learn a replica moved to a different card: the
    deploy history records nothing, because nothing was deployed.

    Empty with nothing staged. A platform with no application deployed has no
    pods to report, and a fixture answering with a creation time it invented
    would let a restart be confirmed against a world that does not exist.
    """
    active = state.active

    if active is None:
        return ArgoCdResourceTree(nodes=[])

    came_up = (
        active.pricing_serving_since
        if application == PRICING_APPLICATION
        else active.serving_since
    )

    if came_up is None:
        return ArgoCdResourceTree(nodes=[])

    if application == PRICING_APPLICATION:
        # One pod, on a node with no GPU: the pricing service serves no model,
        # so its node carries no product label - which is a node, and not a
        # reading that went missing.
        return ArgoCdResourceTree(
            nodes=[
                ArgoCdResourceNode(
                    kind="Pod",
                    name=f"{application}-{_A_POD_SUFFIX}",
                    namespace="production",
                    createdAt=came_up.strftime(TIMESTAMP_FORMAT),
                    info=[ArgoCdInfoItem(name=NODE_INFO_ITEM, value=PRICING_NODE)]
                )
            ],
            hosts=[ArgoCdHost(name=PRICING_NODE, labels={})]
        )

    placements = state.placements()
    nodes = [_the_pod_of(application, placement) for placement in placements]
    autoscaler = state.autoscaler

    if autoscaler is not None:
        # Listed only while one is staged, which is the same condition the
        # manifest route answers a 404 on. A tree that always carried an
        # autoscaler node would have a pin find a controller the platform cannot
        # then describe.
        #
        # Named for the resource and not for the application, which is the one
        # deliberate awkwardness here. Kubernetes does not require an autoscaler
        # to share its target's name, and a fixture that made them equal would let
        # a caller send the application's name as `resourceName` and pass - so this
        # is the shape that catches it, and the name a pin sends has to be the one
        # reported here.
        nodes.append(
            ArgoCdResourceNode(
                kind=AUTOSCALER_KIND,
                name=f"{application}{_THE_AUTOSCALERS_SUFFIX}",
                group=AUTOSCALER_GROUP,
                version=AUTOSCALER_VERSION,
                namespace="production",
                # The same instant the process reports having started, because the
                # autoscaler has been there as long as the deployment has and
                # nothing reads this field for it. A creation time inside the
                # window would read as a controller somebody added during the
                # incident, which is a different incident entirely.
                createdAt=came_up.strftime(TIMESTAMP_FORMAT)
            )
        )

    return ArgoCdResourceTree(
        nodes=nodes,
        hosts=[
            ArgoCdHost(
                name=placement.node,
                labels={GPU_PRODUCT_LABEL: placement.accelerator}
            )
            for placement in placements
        ]
    )


def _the_pod_of(application: str, placement: ReplicaPlacement) -> ArgoCdResourceNode:
    """One replica's pod, as the tree lists it: named, dated, and placed."""
    own_suffix = (
        _THE_PODS_OWN_SUFFIXES[placement.index]
        if placement.index < len(_THE_PODS_OWN_SUFFIXES)
        else str(placement.index)
    )

    return ArgoCdResourceNode(
        kind="Pod",
        name=f"{application}-{_THE_REPLICA_SETS_HASH}-{own_suffix}",
        namespace="production",
        createdAt=placement.started_at.strftime(TIMESTAMP_FORMAT),
        info=[ArgoCdInfoItem(name=NODE_INFO_ITEM, value=placement.node)]
    )


@app.get("/argocd/{application}/resource", response_model=ArgoCdManagedResource)
def argocd_managed_resource(application: str,
                            resourceName: str | None = None,
                            namespace: str | None = None,
                            group: str | None = None,
                            version: str | None = None,
                            kind: str | None = None) -> ArgoCdManagedResource:
    """Stands in for Argo CD's `GET /api/v1/applications/{name}/resource`.

    What is *running*, which is the only place a caller can learn it. The
    repository says how many replicas the deployment is sized for and the
    platform says how many it has, and after one scale-out those are different
    numbers - so a caller deriving a new count, or recording the count it is about
    to replace, has to ask here rather than read the values file.

    A manifest carried as a string, because that is Argo CD's own shape for this
    response: the resource is passed through as text and the caller parses it. A
    stand-in answering a parsed object would be an easier endpoint to write
    against and not the one the adapter will meet.

    `kind` is the one selector this reads, because it is the one that changes the
    answer: a deployment whose size a controller decides has two live resources
    worth asking about, and the count is on one of them while the bounds that
    decide the count are on the other. Every other selector is accepted and
    ignored, as elsewhere in this file - there is one shop, so the resource a
    caller addressed can only be the one there is.

    The pricing service is the exception for the reason it is the exception to a
    scale: the fixture holds no size for it, and answering with the shop's would
    be reporting a neighbour's capacity as its own.
    """
    if application == PRICING_APPLICATION:
        raise HTTPException(
            status_code=400,
            detail=f"{PRICING_APPLICATION} has no managed Deployment to report",
        )

    if kind == AUTOSCALER_KIND:
        return _the_autoscaler_of(application)

    return ArgoCdManagedResource(
        manifest=json.dumps(_the_deployment_of(application))
    )


def _the_deployment_of(application: str) -> dict[str, Any]:
    """The live Deployment, as the platform is holding it.

    Two things about it, and the second is why this is a function rather than a
    literal. `spec.replicas` is how large the deployment is, which is what a
    caller deriving a new count or recording the one it is replacing reads. The
    rest is how far a rolling update has got, which is the only place anybody can
    learn that a deployment *landed and did not finish* - the revision history
    says a sync happened and says nothing about whether the pods turned over.

    A rollout in progress surges, so the fleet is larger than the deployment is
    sized for while it lasts: `status.replicas` counts every pod up and
    `status.updatedReplicas` counts the ones that have reached the new template,
    which is how Kubernetes says the same thing. `spec.paused` says why it is
    holding there.

    A deployment nobody has interrupted answers plainly rather than saying
    nothing: every replica up, every replica updated, and not paused. A channel
    that reported only the unusual case is a channel a reader consults when they
    already suspect the answer, and "it finished" is evidence.
    """
    rollout = state.active.paused_rollout if state.active is not None else None
    replicas = state.replicas

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": application, "namespace": "production"},
        "spec": {
            "replicas": replicas,
            "paused": _is_paused(rollout),
            # Why a paused rollout has more pods up than the deployment asks
            # for. Spelled out on the resource rather than assumed, because a
            # reader who sees six replicas against a spec of three is owed the
            # field that explains it - and this one is the ordinary setting for
            # a service that must not lose capacity while it turns over.
            "strategy": {
                "type": ROLLING_UPDATE_STRATEGY,
                "rollingUpdate": {"maxSurge": "100%", "maxUnavailable": 0},
            },
            # The pod template, and of it only the node selector - the one part
            # of it a caller may change here, and absent until somebody has.
            TEMPLATE_FIELD: {SPEC_FIELD: _the_pod_spec()},
        },
        "status": {
            "replicas": (
                REPLICAS_DURING_A_ROLLOUT if _is_paused(rollout) else replicas
            ),
            "updatedReplicas": (
                REPLICAS_ON_THE_NEWER_SIDE if _is_paused(rollout) else replicas
            ),
            "conditions": [_the_progress_of(rollout)],
        },
    }


def _the_pod_spec() -> dict[str, Any]:
    """The part of the pod template a pin changes: its node selector, where the
    deployment's pods are held to one card, and nothing where they are not."""
    pinned_to = state.accelerator_pin

    if pinned_to is None:
        return {}

    return {NODE_SELECTOR_FIELD: {GPU_PRODUCT_LABEL: pinned_to}}


def _is_paused(rollout: PausedRollout | None) -> bool:
    """Whether the rolling update is stopped where it is.

    Ended rather than cleared is how a rollback closes this stretch - the minutes
    the shop spent split are what happened - so a rollout with an end recorded is
    one that has converged, and reading the field rather than the object's
    presence is what makes a rollback visible here at all.
    """
    return rollout is not None and rollout.ended_at is None


def _the_progress_of(rollout: PausedRollout | None) -> dict[str, str]:
    """What the platform says about this deployment's progress, and since when.

    Kubernetes' own vocabulary, because a reader has to be able to tell a
    deployment that is holding from one that merely has not been touched lately:
    a paused rollout is `Progressing` at status `Unknown` with the reason
    `DeploymentPaused`, and a finished one is `Progressing` at `True` with
    `NewReplicaSetAvailable`. The transition time is what says how long it has
    been that way, which for a paused rollout is the whole of how alarming it is.
    """
    if rollout is None or rollout.ended_at is not None:
        settled = to_bucket_id(_the_deployment_settled_at(rollout))

        return {
            "type": PROGRESSING_CONDITION,
            "status": _PROGRESSING,
            "reason": ROLLOUT_COMPLETE_REASON,
            "lastUpdateTime": settled,
            "lastTransitionTime": settled,
        }

    paused = to_bucket_id(rollout.began_at)

    return {
        "type": PROGRESSING_CONDITION,
        "status": _PROGRESS_UNKNOWN,
        "reason": ROLLOUT_PAUSED_REASON,
        "lastUpdateTime": paused,
        "lastTransitionTime": paused,
    }


def _the_deployment_settled_at(rollout: PausedRollout | None) -> datetime:
    """When the deployment last finished turning over.

    A rollout that was paused and then rolled back converged at the rollback,
    which is the instant a reader wants: it is what says how long the fleet has
    been on one revision, and reporting the process's start instead would date a
    convergence that happened a minute ago to twelve hours back.

    Otherwise the process's own start, which is when the pods serving now came
    up - and for a shop nobody has restarted that is further back than any window
    reaches, which is what a deployment that finished long ago looks like.
    Nothing staged means nothing deployed, and the answer is the same instant an
    unstaged shop reports having started.
    """
    if rollout is not None and rollout.ended_at is not None:
        return rollout.ended_at

    active = state.active

    if active is None or active.serving_since is None:
        return datetime.now(UTC) - SETTLED_UPTIME

    return active.serving_since


@app.post("/argocd/{application}/resource")
def argocd_patch_resource(application: str,
                          body: str = Body(...),
                          resourceName: str | None = None,
                          namespace: str | None = None,
                          group: str | None = None,
                          version: str | None = None,
                          kind: str | None = None,
                          patchType: str | None = None) -> dict[str, str]:
    """Stands in for Argo CD's `POST /api/v1/applications/{name}/resource`.

    The same path the resource is read from, which is the vendor's own
    arrangement: `GET` is `GetResource` and `POST` is `PatchResource`. So a caller
    learns one endpoint and one more verb rather than a second route, and the
    adapter pointed here is the adapter that would be pointed at a real Argo CD.

    **The patch arrives as a JSON-encoded string, not as an object.** That is the
    vendor's shape - the body is declared `string` in its own swagger - and it is
    the mirror of the manifest coming back as text from the `GET`. A stand-in
    taking a parsed object would be an easier endpoint to write against and not the
    one the adapter will meet.

    Two kinds and one field of each. A patch addressed at anything but the
    autoscaler or the Deployment, or carrying anything but the autoscaler's floor
    or the Deployment's GPU node selector, is refused rather than quietly
    accepted: a platform answering 200 to a patch it did not apply would have a
    caller believe production had changed when it had not, which is the same
    reason an unknown resource action is refused above.

    Argo CD answers an empty body on success, and so does this.
    """
    _the_platform_has_to_answer()

    if kind not in (AUTOSCALER_KIND, DEPLOYMENT_KIND):
        raise HTTPException(
            status_code=400,
            detail=(
                f"only a {AUTOSCALER_KIND} or a {DEPLOYMENT_KIND} may be patched "
                f"here, not {kind}"
            ),
        )

    if patchType is not None and patchType != MERGE_PATCH_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported patch type: {patchType}",
        )

    if kind == DEPLOYMENT_KIND:
        if application == PRICING_APPLICATION:
            raise HTTPException(
                status_code=400,
                detail=f"{PRICING_APPLICATION} has no managed Deployment to patch",
            )

        state.pin_to_accelerator(_the_accelerator_in(body))

        return {}

    if state.autoscaler is None:
        raise HTTPException(
            status_code=404,
            detail=f"{application} has no {AUTOSCALER_KIND} deployed",
        )

    state.pin_the_autoscaler_floor_to(_the_floor_in(body))

    return {}


def _the_floor_in(body: str) -> int:
    """The floor the patch asks for, or a refusal.

    Refused four ways, all of them 400, because all four are the same mistake said
    differently: a body that is not a document, a document that does not reach the
    field, a value that is not a number, and a number that is not a count. A real
    server rejects a malformed merge patch the same way, and a caller has to be
    able to tell that from a floor it successfully set.
    """
    try:
        patch = json.loads(body)
        floor = patch[SPEC_FIELD][MIN_REPLICAS_FIELD]
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=(
                f"the patch must be a merge patch setting "
                f"{SPEC_FIELD}.{MIN_REPLICAS_FIELD}"
            ),
        ) from None

    try:
        floor = int(floor)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"invalid number: {floor}",
        ) from None

    if floor < 1:
        raise HTTPException(
            status_code=400,
            detail=f"{MIN_REPLICAS_FIELD} must be at least one, not {floor}",
        )

    return floor


def _the_accelerator_in(body: str) -> str | None:
    """The card the patch holds the pods to, `None` for a patch that releases
    them, or a refusal.

    A merge patch that reaches the GPU label on the pod template's node selector
    and nothing else, with a string to set or a null to remove - RFC 7386's own
    way of deleting a key. Everything else is refused with a 400, for the reason
    a malformed floor is: a caller has to be able to tell a patch the platform
    could not read from one it applied.
    """
    refusal = HTTPException(
        status_code=400,
        detail=(
            f"the patch must be a merge patch setting only "
            f"{SPEC_FIELD}.{TEMPLATE_FIELD}.{SPEC_FIELD}.{NODE_SELECTOR_FIELD}"
            f".{GPU_PRODUCT_LABEL}"
        ),
    )

    try:
        patch = json.loads(body)
        selector = patch[SPEC_FIELD][TEMPLATE_FIELD][SPEC_FIELD][NODE_SELECTOR_FIELD]
    except (ValueError, KeyError, TypeError):
        raise refusal from None

    reaches_only_the_selector = (
        list(patch) == [SPEC_FIELD]
        and list(patch[SPEC_FIELD]) == [TEMPLATE_FIELD]
        and list(patch[SPEC_FIELD][TEMPLATE_FIELD]) == [SPEC_FIELD]
        and list(patch[SPEC_FIELD][TEMPLATE_FIELD][SPEC_FIELD]) == [NODE_SELECTOR_FIELD]
    )

    if (
        not reaches_only_the_selector
        or not isinstance(selector, dict)
        or list(selector) != [GPU_PRODUCT_LABEL]
    ):
        raise refusal

    accelerator = selector[GPU_PRODUCT_LABEL]

    if accelerator is not None and not isinstance(accelerator, str):
        raise refusal

    return accelerator


def _the_autoscaler_of(application: str) -> ArgoCdManagedResource:
    """The live autoscaler's manifest, or a refusal where there is none.

    A 404 and not an empty resource. A platform with no autoscaler deployed has
    none to report, and a fixture that invented bounds would let a pin be
    performed and confirmed against a controller that does not exist - which is
    the one thing a caller reading this cannot check for itself.

    The whole resource rather than the two fields a mitigation writes. The floor
    and the ceiling are what a pin reads, and the target and the scaling behaviour
    are what say *why* this controller misbehaves - so a reader sent to find out
    whether an autoscaler is at fault can see the zero window that makes it one,
    rather than being asked to take the diagnosis on trust.
    """
    autoscaler = state.autoscaler

    if autoscaler is None:
        raise HTTPException(
            status_code=404,
            detail=f"{application} has no {AUTOSCALER_KIND} deployed",
        )

    return ArgoCdManagedResource(
        manifest=json.dumps(
            {
                "apiVersion": f"{AUTOSCALER_GROUP}/{AUTOSCALER_VERSION}",
                "kind": AUTOSCALER_KIND,
                "metadata": {"name": application, "namespace": "production"},
                "spec": {
                    "scaleTargetRef": {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "name": application,
                    },
                    MIN_REPLICAS_FIELD: autoscaler.floor,
                    "maxReplicas": autoscaler.ceiling,
                    "metrics": [
                        {
                            "type": "Resource",
                            "resource": {
                                "name": "cpu",
                                "target": {
                                    "type": "Utilization",
                                    "averageUtilization":
                                        autoscaler.declared.target_cpu_percent,
                                },
                            },
                        }
                    ],
                    "behavior": {
                        "scaleDown": {
                            "stabilizationWindowSeconds":
                                autoscaler.declared
                                .scale_down_stabilization_seconds,
                        }
                    },
                },
            }
        )
    )


@app.patch("/argocd/{application}", response_model=ArgoCdApplication)
def argocd_patch_application(application: str,
                             body: ArgoCdApplicationPatch) -> ArgoCdApplication:
    """Stands in for Argo CD's `PATCH /api/v1/applications/{name}`.

    The one thing a caller changes through it here is the sync policy, because
    a rollback cannot run while an application syncs itself - the platform
    refuses, and would in any case re-apply the revision being rolled away
    from at the next reconciliation. Suspending automated sync is therefore
    part of rolling back rather than a separate concern, and it is the half a
    withdrawal has to put back.

    A merge patch (RFC 7386) only, of `spec.syncPolicy` only. The real route
    also takes a JSON patch, and a patch of anything in the application; this
    application has a sync policy and nothing else that may change, so any
    other patch is refused rather than answered as though it had landed.
    """
    _the_platform_has_to_answer()

    if body.patchType != _A_MERGE_PATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Patch type '{body.patchType}' is not supported here"
        )

    try:
        patch = json.loads(body.patch)
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail=f"the patch is not JSON: {error}"
        ) from error

    if not _touches_only_the_sync_policy(patch):
        raise HTTPException(
            status_code=400,
            detail="only spec.syncPolicy may be patched here"
        )

    patched = _merged({"spec": {"syncPolicy": state.sync_policy}}, patch)
    policy = patched.get("spec", {}).get("syncPolicy") or {}

    try:
        ArgoCdSyncPolicy.model_validate(policy)
    except ValidationError as error:
        raise HTTPException(
            status_code=400, detail=f"not a sync policy: {error}"
        ) from error

    state.set_sync_policy(policy)

    return argocd_application(application)


def _touches_only_the_sync_policy(patch: Any) -> bool:
    if not isinstance(patch, dict) or set(patch) - {"spec"}:
        return False

    spec = patch.get("spec", {})

    return isinstance(spec, dict) and not set(spec) - {"syncPolicy"}


def _merged(target: Any, patch: Any) -> Any:
    """`patch` applied to `target` as RFC 7386 says: an object merges key by key,
    a `null` removes its key, and anything else replaces what was there.
    """
    if not isinstance(patch, dict):
        return patch

    result = dict(target) if isinstance(target, dict) else {}

    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _merged(result.get(key), value)

    return result


@app.post("/argocd/{application}/rollback", response_model=ArgoCdApplication)
def argocd_rollback(application: str, body: ArgoCdRollback) -> ArgoCdApplication:
    """Stands in for Argo CD's `POST /api/v1/applications/{name}/rollback`.

    Returns the application to a revision it has already deployed. Nothing is
    written to the repository - that is the whole reason this is a mitigation
    Argus may take unasked rather than an infrastructure change somebody has to
    approve: the revision being applied was reviewed and run before.

    Refused while the application syncs itself, exactly as the real platform
    refuses it. That is not a limitation being modelled for fidelity's sake -
    it is the fact that makes a rollback honestly a *mitigation*: the values
    file still names the port that broke this, so whatever brought the bad
    revision in will bring it back the moment it is allowed to.

    Refused, too, for a history entry that does not exist. A platform that
    accepted a rollback to a revision it never deployed would have a caller
    believe production had moved when it had not.
    """
    _the_platform_has_to_answer()

    if state.syncs_itself:
        raise HTTPException(
            status_code=400,
            detail=(
                "cannot rollback an application with automated sync enabled - "
                "disable it first"
            ),
        )

    history = _the_revision_history()

    if body.id not in {entry.id for entry in history}:
        raise HTTPException(
            status_code=400,
            detail=f"no revision history entry with id {body.id}",
        )

    if body.id == history[-1].id:
        # The newest entry is the revision the application was already on, so
        # asking for it is not a rollback at all - it is a withdrawal, which is
        # how Argus puts a mitigation back. The endpoint is the platform's own
        # and takes a direction the same way the platform's does: by which
        # history entry it is addressed to.
        state.withdraw_the_rollback()
    else:
        state.roll_the_deployment_back()

    return argocd_application(application)


@app.post("/monitoring/alert", response_model=AlertRaised)
def raise_alert() -> AlertRaised:
    """Fires the alert the shop's monitoring would fire, at whatever is
    listening for it (see `target_app.monitoring`).

    Which alert that is comes from the staged scenario rather than from the
    caller: the rule that trips is a property of what is wrong with the service,
    and a console that could choose it would be choosing the incident's
    symptoms.

    The shop's data-integrity job is asked what it found on the way past, and for
    one scenario that answer is the alert - see `target_app.integrity`. It is
    asked whatever is staged, because the job is the shop's and runs regardless of
    anybody's incident; on a shop whose totals are in order it finds nothing and
    decides nothing, which is the answer a check has to be able to give.

    This is the only caller. The check has no endpoint of its own, so nothing
    outside this process can run it - which is what makes an action taken on this
    incident unconfirmable rather than merely slow to confirm.
    """
    try:
        delivered = fire_alert(
            state.active_scenario_id,
            finding=state.the_integrity_check_found(),
            # Asked whatever is staged, exactly as the integrity check is and for
            # the same reason: a monitoring stack notices a series has stopped
            # without being told which incident is on, and on a shop that is
            # publishing there is no such minute to report.
            unheard_from_since=state.the_minute_the_shop_went_quiet(),
            # And what the same job found when it asked the cache in front of
            # those totals the same question. Asked whatever is staged, as the
            # two above are: a shop whose cache agrees reports nothing, and a
            # shop with no cache has nothing to compare.
            stale=state.the_cache_check_found(),
            promoted_at=state.promoted_at,
            # How this deployment's cache addresses an entry. Handed to the
            # monitoring rather than composed there, because the key format
            # belongs to whatever keeps the cache, and the alert is the only
            # thing that can tell a consumer what to act on.
            address_of=the_key_for
        )
    except AlertNotDelivered as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    # After it was delivered, because an alert nobody received paged nobody -
    # and the on-call provider counts every responder's minutes from this.
    state.somebody_was_paged()

    return AlertRaised(incident_id=delivered.get("incident_id"))


@app.get("/grafana/api/v1/provisioning/alert-rules/{uid}")
def grafana_alert_rule(uid: str) -> JSONResponse:
    """Stands in for Grafana's `GET /api/v1/provisioning/alert-rules/{uid}`:
    what the rule watches, over how long, and its `for` and `keep_firing_for`."""
    rule = alert_rules.SERIES_RULES.get(uid)

    if rule is not None:
        return JSONResponse(content=alert_rules.a_definition(rule))

    title = _the_finding_rule_titled(uid)

    if title is None:
        return JSONResponse(status_code=404, content={"message": "rule not found"})

    # A finding's rule is a check, not a query over a range, so it has no data a
    # caller could read a window off - which is the truth about it.
    return JSONResponse(content={
        "uid": uid, "title": title, "folderUID": alert_rules.FOLDER_UID,
        "ruleGroup": alert_rules.GROUP, "for": "0s", "keep_firing_for": "0s",
        "data": []
    })


@app.get("/grafana/api/v1/provisioning/folder/{folder_uid}/rule-groups/{group}")
def grafana_rule_group(folder_uid: str, group: str) -> JSONResponse:
    """Stands in for Grafana's rule-group route, which carries the interval
    every rule in the group is evaluated at."""
    if folder_uid != alert_rules.FOLDER_UID or group != alert_rules.GROUP:
        return JSONResponse(status_code=404, content={"message": "group not found"})

    return JSONResponse(
        content=alert_rules.a_rule_group(list(alert_rules.SERIES_RULES.values()))
    )


@app.get("/grafana/api/prometheus/grafana/api/v1/rules")
def grafana_rules(rule_uid: str = Query(...)) -> JSONResponse:
    """Stands in for Grafana's `GET /api/prometheus/grafana/api/v1/rules`,
    filtered to one rule: whether it is firing, as of its last evaluation.

    Evaluated now, over the minutes the shop has published - a rule reads what
    its data source holds, so a minute the shop never published is a minute it
    does not see.
    """
    now = datetime.now(UTC)
    rule = alert_rules.SERIES_RULES.get(rule_uid)

    if rule is not None:
        window = state.generated_window()
        rows = [bucket.model_dump() for bucket in _the_buckets()]
        state_now = alert_rules.state_of(
            rule, rows, now, window[1] if window is not None else now
        )

        return JSONResponse(
            content=alert_rules.a_rules_answer(rule.uid, rule.title, state_now, rule)
        )

    title = _the_finding_rule_titled(rule_uid)

    if title is None:
        return JSONResponse(status_code=404, content={"message": "rule not found"})

    # A finding's rule is firing exactly while the alert the shop would raise
    # now is that rule's - the same checks, asked the same way.
    firing = the_rule_linked_from(
        _the_alert_the_shop_would_raise()["alerts"][0]
    ) == rule_uid

    return JSONResponse(content=alert_rules.a_rules_answer(
        rule_uid,
        title,
        alert_rules.RuleState(
            alert_rules.FIRING if firing else alert_rules.INACTIVE,
            alert_rules.last_evaluation_at(alert_rules.HIGH_ERROR_RATE, now),
            None,
            None
        )
    ))


def _the_finding_rule_titled(uid: str) -> str | None:
    return next(
        (title for title, known in FINDING_RULE_UIDS.items() if known == uid), None
    )


def _the_alert_the_shop_would_raise() -> dict[str, Any]:
    """The alert `raise_alert` would send now, built from the same checks."""
    return an_alert_for(
        state.active_scenario_id,
        datetime.now(UTC),
        finding=state.the_integrity_check_found(),
        unheard_from_since=state.the_minute_the_shop_went_quiet(),
        stale=state.the_cache_check_found(),
        promoted_at=state.promoted_at,
        address_of=the_key_for
    )


@app.get("/scenario/status", response_model=ScenarioStatus)
def scenario_status() -> ScenarioStatus:
    return _the_scenario_now()


def _the_scenario_now() -> ScenarioStatus:
    """What is staged, and the instant it was staged at.

    One answer for all three of the endpoints that report the scenario, because
    they report the same fact: seeding it, clearing it and asking about it all
    describe the state the shop is in afterwards. Three copies of the reading
    is three places for one of them to go on naming the scenario without the
    instant it hangs off.
    """
    active = state.active

    return ScenarioStatus(
        active_scenario=state.active_scenario_id,
        seeded_at=active.seeded_at if active is not None else None,
        rule_uid=(
            the_rule_for(state.active_scenario_id).uid if active is not None else None
        )
    )


def _the_log_lines() -> list[str]:
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


@app.get("/scenario/metrics", response_model=list[MetricBucket])
def scenario_metrics() -> list[MetricBucket]:
    """The active scenario's minutes, one row each - the shop's own view.

    What the console draws and what a test reads to know what the shop did.
    A consumer standing where a responder stands reads the same minutes through
    the Prometheus stand-in below instead, in Prometheus's own shape.
    """
    return _the_buckets()


def _the_exposition() -> str:
    """What Prometheus would scrape off this shop: text exposition, current
    values of the series the stand-in's queries name.
    """
    return prometheus.an_exposition([bucket.model_dump() for bucket in _the_buckets()])


# The shop's own routes, answering from the figures this rig generates for it.
app.include_router(the_shops_routes(
    log_lines=_the_log_lines,
    exposition=_the_exposition,
    exposition_content_type=prometheus.EXPOSITION_CONTENT_TYPE
))


@app.get("/prometheus/api/v1/query_range")
def prometheus_query_range(query: str | None = Query(None),
                           start: str | None = Query(None),
                           end: str | None = Query(None),
                           step: str | None = Query(None)) -> JSONResponse:
    """Stands in for Prometheus's `GET /api/v1/query_range`.

    Answers the fixed set of expressions in `prometheus.QUERIES`, from the
    minutes `/scenario/metrics` serves, value for value. A request Prometheus
    would refuse - a parameter missing or unreadable, an expression this does
    not know - is refused in Prometheus's error envelope, with its status.
    """
    try:
        if query is None or start is None or end is None or step is None:
            raise prometheus.BadData("query, start, end and step are all required")

        data = prometheus.a_matrix(
            [bucket.model_dump() for bucket in _the_buckets()],
            query,
            prometheus.read_time(start),
            prometheus.read_time(end),
            prometheus.read_step(step),
            now=datetime.now(UTC),
            reporting_lag_minutes=get_prometheus_settings().reporting_lag_minutes
        )
    except prometheus.BadData as error:
        return JSONResponse(
            status_code=400,
            content={
                "status": prometheus.ERROR,
                "errorType": prometheus.BAD_DATA,
                "error": str(error)
            }
        )

    return JSONResponse(content={"status": prometheus.SUCCESS, "data": data})


def _the_buckets() -> list[MetricBucket]:
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
                p99_ms=minute.p99_ms,
                request_volume=minute.request_volume,
                memory_used_bytes=minute.memory_used_bytes,
                memory_limit_bytes=minute.memory_limit_bytes,
                process_start_time_seconds=minute.process_start_time_seconds,
                cpu_used_cores=minute.cpu_used_cores,
                cpu_limit_cores=minute.cpu_limit_cores,
                cache_hit_ratio=minute.cache_hit_ratio,
                categoriser_confident_ratio=minute.categoriser_confident_ratio,
                fraud_held_for_review_ratio=minute.fraud_held_for_review_ratio,
            )
            for minute in _generated_minutes()
            # The one channel that drops a minute, and it drops it rather than
            # reporting it as zero: a bucket of zeros describes a shop serving
            # nothing, and a reader who could not tell that from a shop nobody
            # is hearing from would diagnose the second as the first. `/logs`
            # above serves the same minutes untouched, which is what says the
            # shop behind the missing rows is well.
            if minute.published
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


# What the provider will hand back at once, and what it hands back when nobody
# says. Both are the real service's - "Limit can range between 1 and 100, and
# the default is 10" - and a stand-in generous about either would license a
# caller the real account then refuses.
THE_SMALLEST_PAGE = 1
THE_LARGEST_PAGE = 100
THE_PAGE_NOBODY_ASKED_FOR = 10

# How the provider refuses a request it will not answer: an `error` envelope
# naming the kind of fault, the parameter at fault, and what was wrong with it.
# The status is the provider's; the sentence is ours, because inventing the
# vendor's own wording would be a lie a reader could come to depend on.
A_REQUEST_THE_PROVIDER_REFUSES = 400
AN_INVALID_REQUEST = "invalid_request_error"


@app.get("/stripe/v1/charges", response_model=StripeList)
def stripe_charges(
    created_gte: int = Query(0, alias="created[gte]"),
    created_lte: int = Query(0, alias="created[lte]"),
    limit: int = Query(THE_PAGE_NOBODY_ASKED_FOR),
    starting_after: str | None = Query(None),
) -> StripeList | JSONResponse:
    """Stands in for Stripe's `GET /v1/charges`.

    The window arrives as the SDK sends it - `created[gte]` and `created[lte]`,
    unix seconds - and paging works as the SDK expects, because the client
    pages to the end of a window and a stand-in that answered everything in one
    page would leave that path untested until a real account was in front of
    it.

    Takings over a minute `/scenario/metrics` still reports come from that minute, so an
    incident that breaks the shop shows up in the money. Over a minute older
    than the metrics reach they are the shop's ordinary trade, because Stripe
    does not expire charges and a stand-in that answered an old window empty
    would be teaching a consumer that no takings and no records are one answer.

    Paged by generating the page rather than by slicing the window, which is
    what lets a window of any age be asked for at all - see
    `target_app.payments.a_page_of_charges`.

    A page the provider would not serve is refused here too. The window is the
    caller's business and the page size is the provider's, and a stand-in that
    answered a thousand charges at once would let a caller solve its round
    trips against a service that will not have it.
    """
    if not THE_SMALLEST_PAGE <= limit <= THE_LARGEST_PAGE:
        return JSONResponse(
            status_code=A_REQUEST_THE_PROVIDER_REFUSES,
            content={
                "error": {
                    "type": AN_INVALID_REQUEST,
                    "param": "limit",
                    "message": (
                        f"limit must be between {THE_SMALLEST_PAGE} and "
                        f"{THE_LARGEST_PAGE}, and {limit} is not"
                    ),
                }
            },
        )

    window_start = datetime.fromtimestamp(created_gte, tz=UTC)
    window_end = datetime.fromtimestamp(created_lte or created_gte, tz=UTC)

    page, has_more = a_page_of_charges(
        _the_buckets(), window_start, window_end, limit, starting_after
    )

    return StripeList(data=page, has_more=has_more)


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
        _the_buckets(),
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


@app.get("/registry/services/{service}", response_model=RegisteredServiceResponse)
def registered_service(service: str) -> RegisteredServiceResponse:
    """What the organisation's service registry records about one service.

    Unlike `/logs`, `/scenario/metrics` and the Argo CD stand-in, this does not answer
    from the staged scenario: the registry says what calls what, which is a fact
    about how the shop is built rather than about what is wrong with it today. It
    answers the same thing with nothing seeded, which is exactly right - the
    coupling was there all along, and that it went unnoticed is the incident.

    A service the registry does not hold answers with no dependencies and its own
    name echoed back, so a reader can tell "nothing recorded" from "nothing
    called".
    """
    found = dependencies_of(service)

    return RegisteredServiceResponse(
        service=found.service,
        dependencies=[
            RegisteredDependency(
                name=dependency.name,
                purpose=dependency.purpose,
                host=dependency.host,
                owner=dependency.owner,
                ownership=dependency.ownership
            )
            for dependency in found.dependencies
        ]
    )


@app.get("/argocd/{application}", response_model=ArgoCdApplication)
def argocd_application(application: str) -> ArgoCdApplication:
    """Stands in for Argo CD's `GET /api/v1/applications/{name}`.

    Answers from whichever scenario is seeded, whatever application it is asked
    about - exactly as `/logs` and `/scenario/metrics` do - but echoes the requested name
    back in `metadata.name`, because a real Argo CD identifies the application it
    was asked for and an adapter is entitled to rely on that.

    A scenario with no deploy returns an empty history rather than an error: no
    deploy is a real answer, and the whole reason this endpoint exists is to let
    a consumer tell an incident a deploy caused from one it did not.

    Being generated is not itself an answer to whether anything was deployed.
    Most generated scenarios stage a state - a flag, a heap, somebody else's
    outage - and deployed nothing; one stages a change, and the change is a
    deploy like any other. So the question asked here is whether the scenario
    has a deploy, not how its telemetry is produced.
    """
    return ArgoCdApplication(
        metadata=ArgoCdApplicationMetadata(name=application, namespace="argocd"),
        spec=_the_spec_now(),
        status=ArgoCdApplicationStatus(history=_the_revision_history()),
    )


def _the_spec_now() -> ArgoCdApplicationSpec:
    """The application's spec, which here is its sync policy and nothing else -
    as it was declared, and as anybody has since patched it.
    """
    return ArgoCdApplicationSpec(
        syncPolicy=ArgoCdSyncPolicy.model_validate(state.sync_policy)
    )


def _the_revision_history() -> list[ArgoCdRevisionHistory]:
    """What this application has had deployed to it, oldest first.

    Two sources, because a deploy can be staged two ways. An authored scenario
    hangs one on whichever of its minutes it landed in; a generated scenario
    that stages a change carries it whole, and is given a parent entry beside
    it - the revision that was running before. The parent is not decoration: a
    rollback is addressed to a history entry, so a history with one entry is a
    history nothing can be rolled back to.
    """
    active = state.active

    if active is None or active.seeded_at is None:
        return []

    if active.scenario.deploy is not None:
        # Where the revision's own stretch is staged, it says when the revision
        # landed - a week back for the drift, which is the instant the oldest
        # affected purchase dates. Every other deploy landed a few minutes ago.
        landed = (
            active.drifting_revision.first_turned_on_at
            if active.drifting_revision is not None
            else active.seeded_at - timedelta(
                minutes=get_scenario_settings().onset_backdate_minutes
            )
        )

        return [
            ArgoCdRevisionHistory(
                id=1,
                revision=active.scenario.deploy.previous_revision,
                deployedAt=to_bucket_id(landed - _A_PREVIOUS_DEPLOY_AGO),
                deployStartedAt=to_bucket_id(
                    landed - _A_PREVIOUS_DEPLOY_AGO - timedelta(minutes=1)
                ),
                source=ArgoCdSource(
                    repoURL=active.scenario.deploy.repo_url,
                    path=active.scenario.deploy.path,
                    targetRevision=active.scenario.deploy.target_revision,
                ),
                initiatedBy=ArgoCdInitiator(username=active.scenario.deploy.initiated_by),
            ),
            ArgoCdRevisionHistory(
                id=2,
                revision=active.scenario.deploy.revision,
                deployedAt=to_bucket_id(landed),
                deployStartedAt=to_bucket_id(landed - timedelta(minutes=1)),
                source=ArgoCdSource(
                    repoURL=active.scenario.deploy.repo_url,
                    path=active.scenario.deploy.path,
                    targetRevision=active.scenario.deploy.target_revision,
                ),
                initiatedBy=ArgoCdInitiator(username=active.scenario.deploy.initiated_by),
            ),
        ]

    if active.scenario.is_generated:
        return []

    span_minutes = scenario_span_minutes(active.scenario)

    return [
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


def _generated_minutes() -> list[GeneratedMinute]:
    """The generated window, built once however many readers want it at once.

    Both telemetry endpoints come through here, and a flag change invalidates
    every minute in the window at once - the memoised minutes are keyed on the
    timeline, so a revert leaves nothing to reuse. Before this gate each reader
    then rebuilt all 360 of them itself, GIL-serialised behind the others:
    measured at 1.32s for one reader, 4.78s for eight and 15.38s for forty,
    which is FastAPI's whole threadpool and past the read tier's ten-second
    budget. The shop times out exactly when an incident is being watched,
    because the polling that watches it is the load.

    Shared by build rather than remembered by minute, which is what keeps the
    reconciliation honest - see `single_flight`. The whole window goes through
    the gate, partial minute and all: waiters get one that is at most a build
    old against a verification loop that looks every ten seconds, where a window
    remembered per minute would report a flag reverted at :30 as still on until
    the minute turned.
    """
    return _the_window_being_built.do(state.active_scenario_id, _the_window_now)


def _the_window_now() -> list[GeneratedMinute]:
    """The window as it stands, read and generated from scratch.

    Run up to whatever instant the scenario has reached: `now` while the
    incident is live and for a settling period after it recovers, and frozen
    afterwards - which is how a finished scenario stops without being cleared.

    Everything the gate above is around, including the two provider reads. They
    are the reason it cannot be a plain mutex: a mutex makes each waiter take
    its own turn at the same work, and forty turns at two blocking reads with a
    five-second timeout is a worse outage than the one it was put there to fix.
    """
    window = state.generated_window()

    if window is None:
        return []

    timeline, up_to = window
    active = state.active
    scenario = active.scenario if active else SCENARIOS[FEATURE_FLAG_TOGGLE]
    settings = get_unleash_settings()

    if scenario.flaps_after_revert and timeline is not None:
        timeline = with_relapses(timeline, up_to)

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
        cache_endpoint=active.cache_endpoint if active else None,
        cache_outage=active.cache_outage if active else None,
        # When a lagging standby was put in front of shoppers, which reaches the
        # window as one log line in that minute and nothing else. It is the only
        # corroboration this incident has in a channel anybody collects: no
        # series moves, because serving a stale figure costs what serving a fresh
        # one costs, so without this the alert's onset is one party's word.
        promoted_at=active.promoted_at if active else None,
        slow_rollout=_the_rollout_in(scenario, timeline),
        slow_deployment=active.deploy_slowdown if active else None,
        pricing_slowdown=active.pricing_slowdown if active else None,
        paused_rollout=active.paused_rollout if active else None,
        demand_surge=active.demand_surge if active else None,
        # Handed in whether or not anything is staged, unlike every condition
        # above it. The others are a scenario's; this is the deployment's own size,
        # which is a fact about the shop with nothing staged as much as with
        # something - and it is what gives CPU a baseline in every window rather
        # than only in the one window it is the subject of.
        capacity=state.capacity,
        # And the controller deciding that size, where one is deciding it. `None`
        # for every scenario but the flapping one, which is what keeps the count in
        # every other window a thing only a person or Argus has moved - see
        # `ScenarioState.autoscaler`.
        autoscaler=state.autoscaler,
        ships_the_statement=scenario.ships_the_statement,
        # Named here and changing nothing below it, which is the point: the flag
        # ships a write path, so a window of this scenario is a window of a well
        # shop. What it buys is that the flag does not also route reads to the
        # monthly summary, which would give the one invisible incident an error
        # rate.
        ships_the_incremental_write=scenario.drifts_the_monthly_total,
        # Named here beside the others and changing nothing about them: what it
        # withholds is the minute itself, not any reading in it.
        scrape_outage=active.scrape_outage if active else None,
        model_upgrade=active.model_upgrade if active else None,
        rescheduling=active.rescheduling if active else None,
    )


def _the_rollout_in(scenario: Scenario,
                    timeline: FlagTimeline | None) -> SlowRollout | None:
    """The stretch a slow feature has been out over, read off the flag's own
    history.

    Derived rather than stored, because it is that history said another way:
    the feature went out the minute the flag went on and came back the minute
    it went off. A second record of it would be one that comes to disagree with
    the first about when somebody reverted - and the flag is the record
    everything else in this service already reconciles against.

    `None` for every scenario that is not this one, which is what keeps the rest
    of them serving pages on the two paths they always took.
    """
    if not scenario.rollout_is_slow or timeline is None:
        return None

    return SlowRollout(
        began_at=timeline.turned_on_at, ended_at=timeline.turned_off_at
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
            p99_ms=entry.p99_ms,
            request_volume=entry.request_volume,
            memory_used_bytes=BASELINE_MEMORY_BYTES,
            memory_limit_bytes=MEMORY_LIMIT_BYTES,
            process_start_time_seconds=process_started_at.timestamp(),
            # An authored minute carries no capacity of its own, so it reports the
            # deployment at rest: the size the values file asks for, at the
            # utilisation the baseline volume puts it at. Nothing authored is about
            # capacity, and a series absent from half the scenarios would be one
            # read as a signal by its presence.
            cpu_used_cores=min(
                the_cores_of(the_deployed_replica_count()),
                the_cpu_demanded_by(entry.request_volume),
            ),
            cpu_limit_cores=the_cores_of(the_deployed_replica_count()),
        )
        for entry in scenario.minutes
    ]


