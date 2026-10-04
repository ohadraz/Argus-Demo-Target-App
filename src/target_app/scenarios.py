"""The scenarios this service can stage, and how each one produces its telemetry.

Two mechanisms sit side by side here, deliberately.

`feature-flag-toggle` is **generated**: seeding it turns a real flag on in a
real provider, and the telemetry is computed from that flag's state whenever
anyone asks. Turning the flag off ends the incident, no matter who turns it off
- which is the only way a mitigation attempt can be honestly graded.

The authored mechanism remains for a scenario that needs a past it can describe
exactly - a fixed list of minutes, anchored so the whole incident sits just
behind the seed instant. Nothing staged here uses it today: every scenario has a
live condition, because a scenario whose telemetry cannot react is one no
mitigation can be graded against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class ScenarioDeploy:
    """A deployment that happened during a scenario's minute.

    Only some scenarios have one - a feature flag flip is not a deploy - and
    that difference is the point: a consumer reading deploy history must be
    able to tell an incident a deploy caused from one it did not.

    `previous_revision` is what was running before it, and it belongs to the
    scenario rather than to whatever assembles the history: a rollback is
    addressed to a history entry, so the entry before this one has to be a real
    commit whose diff against this one is the diagnosis - and which commit that
    is differs per scenario.
    """

    revision: str
    previous_revision: str
    repo_url: str
    path: str
    target_revision: str = "main"
    initiated_by: str = "kuki"


@dataclass(frozen=True)
class ScenarioMinute:
    """One authored minute of a scenario: its log messages and its metrics."""

    offset_minutes: int
    messages: tuple[str, ...]
    error_rate: float
    p50_ms: int
    p95_ms: int
    p99_ms: int
    request_volume: int
    deploy: ScenarioDeploy | None = None


# Which flag stages a generated scenario. `FEATURE` is the new feature that
# breaks when it is switched on; `FALLBACK` is the safe path that breaks when
# it is switched off.
FEATURE_FLAG = "feature"
FALLBACK_FLAG = "fallback"


def quiet_state_for(flag_role: str | None) -> bool:
    """The state a flag in this role sits in when nothing is going on.

    A feature flag is quiet when it is off; a fallback, which has been on for
    months and which nobody thinks of as a change, is quiet when it is on. It
    is a property of the flag's role rather than of any scenario - the shop
    knows where its own flags rest whether or not anything is staged.

    Quiet is deliberately weaker than well. A flag away from its quiet state is
    a flag somebody moved, which is a reason to look at it and not a verdict on
    it: the ambiguous scenario stages two of them at once, exactly one of which
    is breaking anything. What the shop is entitled to say is which flags have
    moved; whether that is the fault is the reader's judgement.
    """
    return flag_role == FALLBACK_FLAG


def description_for(flag_role: str | None) -> str:
    """What the flag is for, in the words the provider's own console shows.

    Two flags with two stories, and both are read by a human standing in front
    of Unleash during a demo. Kept here beside the role rather than in the flag
    client, which is scoped to one flag and has no way to know which it holds.
    """
    if flag_role == FALLBACK_FLAG:
        return (
            "Keeps account pages on the old, safe renderer - on for months, "
            "and the shop's fault is exposed when it is withdrawn"
        )

    return "Average spend per item this month - the demo's seeded fault"


@dataclass(frozen=True)
class ScenarioFamily:
    """What kind of incident a scenario stages, as a console groups them.

    A flat list of scenarios stops being scannable at about six, and there are
    more than that already. Grouping them is therefore presentation - but which
    groups is not a presentation decision, so these are the families of the
    published taxonomy Argus's own backlog is keyed to rather than whatever
    reads tidily beside a radio button.

    `taxonomy` names the published family and the share of real incidents it
    accounts for. Three of the families below sit inside change-induced and all
    three say so, which is the honest rendering: a reader who adds the three
    percentages up and gets ninety-three has been misled by a number printed
    once per group, so the group says whose number it is quoting.

    `blurb` says what the family has in common and, where it has a mirror pair
    in it, what separates them - which is the question an audience is actually
    watching an agent answer.
    """

    id: str
    name: str
    taxonomy: str
    blurb: str


FLAG_CHANGES = ScenarioFamily(
    id="flag-changes",
    name="Flag changes",
    taxonomy="Config-induced failure (FM-10), inside change-induced - 31% of incidents",
    blurb=(
        "Somebody moved a flag. What differs between these is whether it was "
        "the cause, a coincidence, or one of two changes the evidence supports "
        "equally well."
    ),
)
RELEASES = ScenarioFamily(
    id="releases",
    name="Releases",
    taxonomy="Deploy-induced regression (FM-09), inside change-induced - 31% of incidents",
    blurb=(
        "A revision went out. No log line mentions it, so the deploy history "
        "is the only evidence naming a cause."
    ),
)
CONFIGURATION = ScenarioFamily(
    id="configuration",
    name="Configuration",
    taxonomy="Config-induced failure (FM-10), inside change-induced - 31% of incidents",
    blurb=(
        "A value, not a version. The change rode out with a deployment, and "
        "the shop is now asking an address nothing answers on."
    ),
)
NEIGHBOURS = ScenarioFamily(
    id="neighbours",
    name="Neighbours",
    taxonomy="Propagation - 28% of incidents",
    blurb=(
        "Another service's fault, arriving here. What separates escalating "
        "from mitigating is ownership, which the service registry answers and "
        "no telemetry does."
    ),
)
CAPACITY = ScenarioFamily(
    id="capacity",
    name="Capacity & resource",
    taxonomy=(
        "Resource exhaustion (FM-13) and autoscaling pathology (FM-25) - 13% of "
        "incidents"
    ),
    blurb=(
        "A finite resource is going faster than it is replenished. Whether "
        "consumption moved with the traffic decides between reclaiming what "
        "accumulated and adding capacity the deployment never had - and if the "
        "capacity is itself moving, neither: what needs stopping is whatever "
        "keeps taking it away."
    ),
)
THE_TAIL = ScenarioFamily(
    id="the-tail",
    name="Hidden in the tail",
    taxonomy=(
        "Aggregate-masked tail degradation (FM-06), inside tail/outlier - 3% of "
        "incidents"
    ),
    blurb=(
        "Nothing fails and no aggregate a monitoring stack watches moves. The "
        "incident exists in the 99th percentile and nowhere else."
    ),
)
FOUNDATIONAL_INTEGRITY = ScenarioFamily(
    id="foundational-integrity",
    name="Foundational integrity",
    taxonomy=(
        "Silent data corruption (FM-26), monitoring blind spot (FM-27), "
        "control-plane failure (FM-30) and state divergence (FM-31), inside "
        "foundational integrity - 12% of incidents"
    ),
    blurb=(
        "What is wrong here is the ground the rest of it stands on: what the "
        "shop recorded, whether it recorded anything at all, or whether the "
        "platform a fix has to travel through will act. In three of them "
        "nothing fails and nothing slows, and no rule watching a series will "
        "ever fire - and something of the incident outlives its own cause, so "
        "putting the change back stops the next wrong total and repairs none of "
        "the ones before it, restores the shop's sight without restoring the "
        "minutes it was blind for, and discards the stale figures without "
        "mending the replication that produced them. One is a shop that wrote "
        "the wrong thing down; one wrote nothing down at all; one wrote "
        "everything correctly and is reading it back from a copy that stopped "
        "keeping up. The fourth is the only one here whose pages really do "
        "fail, and what is foundational about it is not the shop: the platform "
        "that every mitigation but one goes through has stopped answering, so "
        "the incident is ordinary and the means of ending it are gone."
    ),
)
HALF_ROLLED_OUT = ScenarioFamily(
    id="half-rolled-out",
    name="Half rolled out",
    taxonomy=(
        "In-flight compatibility break (FM-35), inside tail/outlier - 3% of "
        "incidents"
    ),
    blurb=(
        "A fault that exists for part of the traffic and no more of it, for "
        "reasons that are arithmetic rather than accidental. What fails is "
        "whatever crosses between two versions of the shop, so the failing "
        "share is a product of two shares - nothing at either end of a rollout, "
        "and most of it in the middle."
    ),
)

# The order a console draws them in: by the share of real incidents each family
# accounts for, largest first. Not by how interesting the scenario is to watch -
# the rail is the one place the shop says what kinds of incident exist at all,
# and an order chosen for the demo would quietly re-rank the taxonomy.
FAMILY_ORDER: tuple[ScenarioFamily, ...] = (
    FLAG_CHANGES,
    RELEASES,
    CONFIGURATION,
    NEIGHBOURS,
    CAPACITY,
    FOUNDATIONAL_INTEGRITY,
    THE_TAIL,
    HALF_ROLLED_OUT,
)


@dataclass(frozen=True)
class Scenario:
    """What a scenario is, from the outside.

    `minutes` is empty for a generated scenario, and that emptiness is what
    selects the mechanism: there is nothing authored to serve, so the generator
    is asked instead.

    The three fields after it describe a generated scenario's live condition,
    and exist so that "a flag change caused this" can be staged in either
    direction and with either outcome:

    - `flag_role` says which flag stages it.
    - `breaks_when_flag_is_on` says which way that flag has to move to break
      the shop. False means the incident *starts* when the flag goes off,
      which is what a withdrawn kill switch looks like.
    - `recovers_when_flag_reverts` says whether putting the flag back ends the
      incident. False stages a coincidence: a flag really did change and the
      logs really do show it, but something else is breaking the shop, so
      reverting the flag changes nothing. That is the case an agent must be
      able to be *wrong* about and notice.

    `decoy_flag_role` names a *second* flag that moves at the same instant and
    has nothing to do with the fault. It exists so an incident can be genuinely
    ambiguous rather than merely wrong: with two flags changed in the same
    minute, both touching the failing page, the evidence supports two
    explanations and choosing between them is a judgement instead of a lookup.
    Reverting the decoy changes no telemetry at all, so an agent that tries it
    learns the ordinary lesson of an incident - that the first correlated change
    was not the cause - and still has somewhere to go next.

    `leaks` stages a scenario of the other generated kind: its live condition is
    the serving process's own accumulation rather than a flag's state. No flag
    is touched, nothing anybody does to a flag ends it, and what does end it is
    a restart - which reclaims the heap and leaves the fault in the code, so the
    climb begins again. That is what makes it the first scenario Argus can
    mitigate without resolving.

    `upstream_fails` stages the third generated kind, and the only one whose
    live condition belongs to somebody else: the payment provider the account
    page asks for a shopper's card stops answering. No flag is touched and no
    restart reclaims anything, because neither the value nor the process is
    what is wrong - which is what makes it the one scenario where the correct
    outcome is that Argus takes nothing and hands the incident to a person.

    `cache_is_misconfigured` stages the fourth generated kind, and the only one
    where nothing is broken at all. The shop's summary cache is up and
    answering; a configuration change pointed the shop at the wrong port, so
    every lookup fails to connect and every page recomputes. The page is still
    correct - the fallback is the designed behaviour - so the error rate never
    moves, and because nine requests in ten were cached, the tail already
    described a recomputed page and barely moves either. Only the median steps.
    That makes it the one scenario an aggregate-watching monitor cannot see,
    and the one whose fix is a value rather than a toggle, a restart or a
    commit.

    `rollout_is_slow` stages the fifth generated kind, and the only one where
    the shop is neither broken nor slow. It is slow for three requests in a
    hundred: the account page's newest figure went out to a small canary, and
    the path it takes walks the shopper's purchase history once per item. Those
    pages are correct - it is the same figure, worked out the long way - so the
    error rate never moves, and three in a hundred is below the 95th percentile
    by arithmetic, so neither does the tail a monitoring stack watches. What
    moves is the 99th percentile, by an order of magnitude.

    That makes it the mirror of `cache_is_misconfigured`, deliberately. One
    incident hides in the tail because the tail already described a slow
    request; the other hides in the p95 because it never reaches that far down
    the distribution. Neither is visible in the other's aggregate, and both are
    served by the same cache mixture. What separates them is what ends them: a
    rollout is a flag somebody moved, so this one is put right by the first
    mitigation Argus ever had - and unlike the leak and the misconfiguration, it
    is resolved as well as mitigated, because nothing is left behind for a new
    process or a re-sync to find.

    `dependency_is_slow` stages the seventh generated kind, and the only one
    whose live condition belongs to a service this company runs and this process
    is not. The pricing service every account page asks what a basket comes to
    starts taking an order of magnitude longer to answer. It answers every call,
    so nothing fails; the shop waits on every request, because every page shows
    a basket total, and the median and both tails climb together.

    Its telemetry is nearly `deploy_is_slow`'s, and the one arithmetic
    difference between them is worth reading rather than smoothing away. A
    slower revision *multiplies* what every request cost, so the spread grows
    with the centre - 45ms to 450ms and 380ms to 3800ms. A wait *adds* to it, so
    the spread collapses: 45ms to about 1550ms and 380ms to about 1720ms, the
    median and the tail within a couple of hundred milliseconds of each other.
    That is what a queue behind one slow call actually looks like, and a reader
    who notices it has found the incident from the metrics alone.

    What separates them for certain is the deploy history: one has an entry at
    the onset and this has none. What names the cause is a WARN line the shop
    writes
    about its own outbound call, and what decides the response is the service
    registry, which says the host in that line belongs to this company while the
    payment provider's, which looks exactly as foreign, does not.

    It is the mirror of `upstream_fails`, and the pair is the point. Both are a
    neighbour's fault arriving here; one is answered by escalating because
    nothing Argus may do reaches another company, and this one by restarting a
    service Argus was not paged about. Restarting the shop changes nothing,
    which is what makes the wrong answer refutable rather than accidentally
    right.

    `surges` stages the eighth generated kind, and the only one where nothing
    about the shop is wrong at all. Its code, its configuration, its flags, its
    heap and its neighbours are all exactly as they were; what changed is how many
    shoppers arrived. The traffic climbs to several times the volume three
    replicas were sized for, every request begins queueing for a core that is
    already busy, and the median and both tails climb together.

    Its telemetry is `deploy_is_slow`'s in every judged series, which is the point:
    a multiplied cost on every request looks the same whether the cost went up or
    the number of requests did. Three things separate them, and all three are
    retrievable. The deploy history is empty. The reported volume moved, and moved
    first. And utilisation is pinned at capacity, which is what says the resource
    ran out rather than the work getting more expensive.

    It is the mirror of `leaks`, and that pair is the whole reason the mode exists.
    Both are a finite resource being consumed faster than it is replenished; one
    is consumption climbing while the traffic does not, answered by reclaiming what
    accumulated, and this is consumption the traffic asked for, answered by adding
    capacity. Restarting changes nothing here - demand and capacity are both where
    they were - which is what makes the wrong answer refutable rather than
    accidentally right.

    It is also the only scenario in this file that nothing ends. A flag goes back,
    a process comes up, a revision is returned and a neighbour recovers; shoppers
    do not go away because somebody scaled a deployment. So this one is mitigated
    by making the shop bigger and is never resolved - and a withdrawal that puts
    the count back returns it to saturation.

    `autoscaler_flaps` stages the ninth generated kind, and the only one whose
    fault is a control loop rather than a change or a state. It is the surge's
    traffic exactly, with one difference: this deployment has an autoscaler, and
    the autoscaler's scale-down stabilisation window is zero. So the controller
    scales up on a saturated minute, the minute after is still served at the old
    capacity while the new replicas come ready, the third runs at the ceiling and
    reports a fraction of its target, and the controller takes the replicas away
    again. Three minutes, repeating, and the capacity a minute was served at is
    the series that says so.

    Two things make it the sharpest pair in the file. Its telemetry at the bottom
    of every cycle *is* `surges`'s telemetry, so the near-miss diagnosis is
    genuinely tempting rather than straw - what separates them is one series
    moving. And it is the only scenario that undoes something Argus did: a
    scale-out holds for a minute and is then re-derived away, which is what makes
    adding capacity the wrong answer here and refutable rather than merely
    unhelpful. Until this scenario existed, nothing in the fixture had ever put a
    replica count back.

    What ends it is raising the controller's floor to its ceiling, which leaves it
    nowhere to scale down to without removing it. The values file still declares
    the window that flaps, so this is mitigated and never resolved, and putting
    the floor back returns the shop to flapping.

    `rollout_is_paused` stages the tenth generated kind, and the only one where
    no revision is at fault. A revision that changes the shape of what the
    summary cache stores was deployed and its rolling update was paused
    half-way, so half the fleet writes the new shape and the other half cannot
    read it. An account page fails when a replica on the older side draws an
    entry a replica on the newer side wrote, and that is the whole of what
    fails: the requests that cross between the two versions now serving.

    The failing share is therefore a product of two shares - written by the
    newer side, read by the older - scaled by how much of the traffic the cache
    answers at all. Zero before a rollout starts, zero once it finishes, and
    largest in the middle, which is arithmetic no other scenario here produces
    and which a reader can check against the replica counts the platform
    reports.

    Its telemetry is `feature-flag-toggle`'s: an error rate that steps and
    latency that does not move at all. That collision is the difficulty, and
    what separates them is the change channel and nothing else - a flag moved
    for one, a deployment landed for the other. What then separates it from a
    bad deployment is that the deployment did not *finish*, which is a fact
    about the rollout rather than about the code it carried, and which nothing
    but the live Deployment can say.

    Both revisions leave `tests/io_shop` green, so there is no defect for a
    patch to turn green and no culprit commit to name. What is left to fix
    afterwards is not a file: the migration needed a version that could read
    both shapes before one that wrote only the new one, and no patch of either
    revision supplies that. So it is mitigated by returning the deployment -
    which ends the incident by *converging* the fleet rather than by removing
    anything - and never resolved, because the repository still declares the
    revision that was going out.

    `ships_the_statement` stages the same incident as `feature-flag-toggle` in
    every respect a reader of the telemetry could name - the same flag, the same
    cohort, the same shoppers failing for the same reason, the same error rate -
    and differs in the one thing no telemetry shows: which file the fault is in.
    The monthly summary's divisor lives in a sixty-line module; the statement's
    empty month lives in the largest module the shop has. That is the whole
    scenario. It exists so that a fix for it is a *large* fix, which is the one
    shape nothing else here produces, and it is the reason this scenario is not
    offered in the console: an audience shown two identical incidents learns
    nothing from the second.

    `drifts_the_monthly_total` stages the eleventh generated kind, and the only
    one where nothing about the shop is unwell at any moment a monitor could
    read. A flag turned on a cheaper way of writing a purchase down, and that
    way stopped adding the purchase to the running total of what its shopper has
    spent this month. So the stored total falls further behind the purchases
    behind it with every sale, the lifetime figure stays right because it derives
    its own total from the list, the monthly figure on the same page is quietly
    low, and nothing throws, nothing waits and no series moves at all.

    It is the only scenario here that no series can show and no series can show
    the end of, which is why it is the only one the shop has to *tell* anybody
    about: a job of the shop's own re-adds the purchases, finds the totals that
    disagree, and fires an alert carrying the count, the widest gap and the
    oldest purchase the gap can be made of. That last figure is the whole of what
    dates the incident. The job runs weekly, so when it found this says nothing
    about when the writing went wrong.

    It is also the only scenario nothing here can put right. Returning the flag
    stops the next purchase being mis-recorded and corrects not one of the
    thousands already written, so re-running the check afterwards finds exactly
    the same accounts and no affected purchase newer than the flip - which is
    what recovery means for this mode and is nothing any graph shows. A restart
    changes nothing whatsoever: the fault is in what was written, and a fresh
    process reads back the same wrong totals. What is owed at the end of it is a
    fix to the write path and a one-off repair of the data, and the repair is
    nobody's to run without being asked.

    `cache_failed_over` stages the thirteenth generated kind, and the only one
    where nothing the shop did is wrong at all. The summary cache lost its
    primary and a standby that had stopped receiving updates three hours earlier
    was promoted in its place. So the cache now serves, as current, the figures
    it held when replication broke: a shopper who has bought anything since sees
    a monthly total frozen before those purchases, printed beside the purchases
    themselves. The ledger is untouched and correct, every page renders, nothing
    throws and no series moves.

    It is `drifts_the_monthly_total`'s mirror, and the pair is the reason both
    are worth having. There the authoritative copy is the wrong one - the stored
    total was mis-written and re-reading it will not help - and no mitigation can
    repair it. Here the authority never moved and the *copy* is wrong, so there
    is a complete fix and Argus can perform it: discard the entries the check
    named, and the page works the figure out from the purchases as it does for
    any shopper the cache has nothing for. Which copy is lying is the whole
    diagnosis, and a reader who confuses the two reaches an action that repairs
    nothing.

    The set of wrong entries grows while the incident runs. An entry is stale
    from the moment its shopper buys something after replication broke, and the
    shop takes an order every `SOMEBODY_BUYS_EVERY` - so the count climbs, and
    the keys an alert carries are what was stale when the check ran rather than
    what is stale now. That is what a promoted stale replica really does, and it
    is why discarding ends the incident without resolving it: the entries named
    are gone and rebuild correctly, and nothing has repaired the replication that
    let a lagging replica be promoted.

    `stops_publishing_telemetry` stages the twelfth generated kind, and the only
    one where what is wrong is the shop's own account of itself. The flag turns
    off telemetry publishing. The shop serves every request correctly, its logs
    go on reporting every minute, and `/scenario/metrics` carries rows up to the minute
    the flag moved and none at or after it. Nothing throws, nothing waits, and
    no series moves - because for those minutes there is no series.

    It is the opposite of `drifts_the_monthly_total` in the one way that matters
    to a reader, and the two sit in the same family for it. There every minute is
    present and sitting at its baseline, and what is wrong is what the shop
    wrote. Here the minutes are missing and nothing the shop wrote is wrong. Both
    are incidents no threshold can fire on; the first is found by a job that
    re-reads the data, the second by a rule that notices a series which was
    reporting has stopped.

    Returning the flag restores publishing, and the minutes lost while it was on
    are lost for good - nothing retains an unpublished minute. So recovery here
    is the sight coming back rather than a level coming down, and a restart
    changes nothing at all: the flag is still on afterwards and the minutes are
    still missing.

    `deploy_is_slow` stages the sixth generated kind, and the only one whose
    live condition is which revision is deployed. The revision that went out
    computes a shopper's lifetime average by walking their purchases once per
    purchase rather than dividing the total the account carries; the figure is
    unchanged and costs ten times as much to produce. This shop has no summary
    cache configured, so every page works its own figure out and every request
    pays - which is why the median, the 95th and the 99th all climb together.

    That makes it the opposite of `rollout_is_slow` in the one way that matters
    to a reader: nothing hides. Detection is trivial and attribution is the
    whole difficulty, because no log line mentions the release and the deploy
    history is the only evidence naming a cause. What ends it is returning the
    deployment to the revision before it, which leaves the slower code on the
    branch - so it is mitigated and not resolved, and re-enabling the platform's
    own reconciliation brings it straight back.

    `control_plane_is_down` stages something no other field here does: not an
    incident, but a platform that will not carry a mitigation. The deployment
    platform's acting routes refuse while it holds and its reporting routes go
    on answering - so the change history still names the deployment, the
    deployment is still a candidate, and what fails is reaching for it. A
    condition that refused the reads as well would hide the change and stage a
    shop nobody can diagnose, which is a different mode and not this one.

    `family` is what kind of incident this stages, and it has no default. A
    default would put every scenario written from here on into whichever family
    happened to be first, silently, and a grouping nobody chose is worse than no
    grouping at all.

    `offered_in_console` is presentation only. A scenario kept for the capability
    it pins down is not automatically one worth showing an audience; hiding it
    leaves it seedable by id, which is how the e2e suite stages it.
    """

    id: str
    title: str
    description: str
    family: ScenarioFamily
    minutes: tuple[ScenarioMinute, ...] = ()
    flag_role: str = FEATURE_FLAG
    breaks_when_flag_is_on: bool = True
    recovers_when_flag_reverts: bool = True
    decoy_flag_role: str | None = None
    leaks: bool = False
    upstream_fails: bool = False
    cache_is_misconfigured: bool = False
    rollout_is_slow: bool = False
    deploy_is_slow: bool = False
    dependency_is_slow: bool = False
    surges: bool = False
    autoscaler_flaps: bool = False
    rollout_is_paused: bool = False
    drifts_the_monthly_total: bool = False
    ships_the_statement: bool = False
    stops_publishing_telemetry: bool = False
    control_plane_is_down: bool = False
    cache_failed_over: bool = False
    # The deploy a *generated* scenario stages, for the one whose cause is a
    # change rather than a state. An authored scenario carries its deploys on
    # its minutes; a generated one has no minutes to hang them on, and a
    # generated scenario caused by a configuration change needs the change to
    # be readable somewhere - so it is here, and the revision it names is a
    # real commit whose diff against its parent is the diagnosis.
    deploy: ScenarioDeploy | None = None
    offered_in_console: bool = True

    @property
    def is_generated(self) -> bool:
        return not self.minutes

    @property
    def stages_a_flag(self) -> bool:
        """Whether this scenario's incident is a flag's doing.

        Most generated scenarios are not: a leak is the process's own
        accumulation, an upstream failure is another company's service, a
        misconfigured cache is a value in a file, a bad deployment is the
        revision that is running, a slow dependency is another team's process,
        and a monitoring blind spot is the name of a port. A page offering a
        flag to watch for any of them would be
        offering a control that changes nothing, and naming a flag as the thing
        that breaks the shop would be pointing at a suspect the fixture
        invented.
        """
        return self.is_generated and not (
            self.leaks
            or self.upstream_fails
            or self.cache_is_misconfigured
            or self.deploy_is_slow
            or self.dependency_is_slow
            or self.surges
            or self.autoscaler_flaps
            or self.rollout_is_paused
            or self.stops_publishing_telemetry
            or self.drifts_from_a_deployment
        )

    @property
    def drifts_from_a_deployment(self) -> bool:
        """Whether the monthly total's drift was shipped by a revision rather than
        turned on by a flag.

        Read off the two things the scenario already says rather than declared
        beside them: the drift, and a deploy to carry it. The flag scenario has the
        first and not the second, and a third field that could disagree with both
        would be a fixture able to stage an incident nobody could have caused.
        """
        return self.drifts_the_monthly_total and self.deploy is not None

    @property
    def healthy_flag_state(self) -> bool:
        """The flag state in which the shop is well - the state a scenario is
        staged *away* from, and the one a reset returns to."""
        return not self.breaks_when_flag_is_on


FEATURE_FLAG_TOGGLE = "feature-flag-toggle"
BAD_DEPLOYMENT = "bad-deployment"
RESOURCE_LEAK = "resource-leak"
UPSTREAM_DEPENDENCY_FAILURE = "upstream-dependency-failure"
CACHE_MISCONFIGURED = "cache-misconfigured"

# The commit that moved the cache's port in `deploy/values-production.yaml`,
# and its parent. Real commits in this repository: the diagnosis is the diff
# between them, so these are looked up rather than invented, and rewriting this
# repository's history would break the one scenario that reads it.
#
# Filled in after the commit exists, which is why it is a constant here and not
# a literal in the scenario - the commit cannot name itself.
# The commit that renamed the metrics port in `deploy/values-production.yaml`,
# and its parent. Real commits in this repository, looked up rather than
# invented for the reason the cache pair below is: the diagnosis is the diff
# between them.
#
# The rename is the whole incident and it reads as housekeeping - every other
# port in the file is named for its protocol, and this one was made to match.
# Nothing in the shop reads the name; the scrape config does, and it selects
# the target by it. So the shop went on serving its metrics endpoint to a
# platform that had stopped asking.
THE_COMMIT_THAT_RENAMED_THE_METRICS_PORT = "9f4ad14582c99c497eb6c2d2af87566cd4493020"
THE_COMMIT_BEFORE_THE_RENAME = "e283ba3af732fc4285166a060b827b1305cd30d9"

THE_COMMIT_THAT_MOVED_THE_CACHE_PORT = "0d8e826225f0de73958a8a8dd3d867b2ae249e72"
THE_COMMIT_BEFORE_IT = "544cef36a8eaf45c5b030c3d5c21473d8176cef3"
# The revision the bad deployment shipped: the lifetime average derived from
# the purchases once per purchase. A literal for the reason the cache port's
# commit is one - the commit cannot name itself.
THE_COMMIT_THAT_SLOWED_THE_AVERAGE = (
    "5e07d73148d0a704b8fefe5f379bc652bb773655"
)
THE_COMMIT_BEFORE_THAT_ONE = "70dbcfde2b549d110a3817d92d60b6dd9786e78b"
# The revision deployed into the control-plane outage: the one that decides
# which purchases fall in this month from when each was recorded rather than
# from the flag the writer set, so that its diff owns the divisor the monthly
# average is dividing by. It sits on a branch of its own and is never merged -
# what the shop actually runs is `main`, where the monthly average is what the
# fix corpus expects to patch, and a deployed revision only has to be a commit
# the change channel can compare against, not one the history leads to.
THE_COMMIT_THAT_MOVED_THE_MONTH_BOUNDARY = (
    "3398e10e131ea6c16f468f1bc1ac0fa6426d1b0c"
)
THE_COMMIT_BEFORE_THE_MONTH_BOUNDARY_MOVED = (
    "f0bcdb929bc6e89981742d03b02f36a40cd19ca0"
)
# The revision being rolled out when the rolling update was paused: the one that
# changed the shape of what the summary cache stores. Constants for the reason
# the two pairs above are - the commit cannot name itself - and read as a pair
# for a reason the others do not share. Neither of these is at fault. The diff
# between them is the diagnosis because it shows a stored shape that changed
# with no read path kept for the old one, which is the expand step of an
# expand-contract migration that nobody performed; both leave `tests/io_shop`
# green, and what is wrong is that the two of them are serving at once.
THE_COMMIT_THAT_RESHAPED_THE_CACHE_ENTRY = (
    "696c33a68b36aed6456cdc5b3806f33038488515"
)
THE_COMMIT_BEFORE_THE_RESHAPE = "5470c1a64205bb28f9f2e8a96dc6ffa5eb2e611e"
# The revision that stopped adding a purchase to its shopper's monthly total, and
# the one before it. Real commits, pushed, and the diff between them is the
# diagnosis: it shows the addition removed and a comment reasoning that the figure
# is derived from the purchases - which is true of the lifetime average and false
# of the monthly one.
#
# **Nothing serves these two to anybody, unlike every pair above them.** Those are
# read by a `ScenarioDeploy` and reach a consumer through the deploy history; this
# scenario carries no deploy, because a flag turned the write path on and a
# deployment did not, and a history entry at the onset would stage a deploy-caused
# incident instead of this one. They are written down so that a person - or a
# grader applying a patch - can find the two revisions the fault lives between.
THE_COMMIT_THAT_DROPPED_THE_MONTHLY_ROLLUP = (
    "83cf54d6c49fa0371449e045b59209eb97507ce9"
)
THE_COMMIT_BEFORE_THE_ROLLUP_WENT = "f61363044ee33fc1b8d3a3b519bcc6a1f2ef7648"
# The same fault reached by a deployment rather than a flag: the revision that
# stopped `record_purchase` itself moving the monthly total, with no flag
# consulted, and the one before it. Served through the deploy history like the
# pairs above the flag scenario's, and on a branch of its own that is never
# merged, as the month-boundary pair is - main still carries the fault behind
# the flag, which is what the fix corpus patches, and a deployed revision only
# has to be a commit the change channel can compare against.
THE_COMMIT_THAT_STOPPED_CARRYING_THE_MONTH = (
    "26f1d7e2c82ce2abff8f9b6424dc226f4f37fed2"
)
THE_COMMIT_BEFORE_THE_MONTH_STOPPED_BEING_CARRIED = (
    "4c810f22894e612ced87f5ee3f3062dfeb785050"
)
FALLBACK_DISABLED = "fallback-disabled"
FLAG_TOGGLE_RED_HERRING = "flag-toggle-red-herring"
COMPETING_FLAG_CHANGES = "competing-flag-changes"
SLOW_CANARY_ROLLOUT = "slow-canary-rollout"
MONTHLY_STATEMENT_PANEL = "monthly-statement-panel"
PRICING_SERVICE_DEGRADED = "pricing-service-degraded"
# Named for the resource rather than for the response, unlike the failure mode it
# reaches: this is one way of arriving at demand saturation, and the next one under
# it - a spike answered by shedding load rather than by adding capacity - is
# another. The same relation `cache-misconfigured` has to a config-induced failure.
CPU_SATURATION = "cpu-saturation"
# Named for the controller rather than for the metric, unlike the scenario above
# it: what is wrong here is not which resource ran short but that the thing
# deciding how much of it there is will not settle. The same relation
# `cpu-saturation` has to demand saturation, one level up.
AUTOSCALER_FLAPPING = "autoscaler-flapping"
# Named for the rollout rather than for what broke, unlike every scenario above
# it. Nothing broke: two correct revisions are serving at once, and the only
# thing that is wrong is that the deployment carrying one of them stopped
# half-way.
HALF_FINISHED_ROLLOUT = "half-finished-rollout"
# Named for what is wrong with the data rather than for what caused it, unlike
# every flag scenario above. A flag is what turned it on, and saying so in the
# name would put the answer in the id of the one incident whose cause nothing in
# the telemetry can reach.
SILENT_DATA_CORRUPTION = "silent-data-corruption"
# The same incident with a deployment where the flag was, and named for the data
# for the same reason: saying "deployment" in the id would put the answer in it,
# and the answer - which change to undo - is the one thing this scenario exists
# to ask.
MONTHLY_TOTALS_FALLING_BEHIND = "monthly-totals-falling-behind"
# Named for the platform rather than for the incident, unlike every scenario
# above it. What breaks the shop here is an ordinary flag change; what the
# scenario stages is that four of the five things Argus could do about one are
# unavailable at once, because the platform all four go through is not
# answering - and that has nothing to do with this incident or any incident.
CONTROL_PLANE_UNREACHABLE = "control-plane-unreachable"
# Named for what Argus loses rather than for what the shop does, unlike every
# scenario above it - including the one it shares a family with. `silent-data-
# corruption` is named for the state of the data because a flag is what turned it
# on and saying so would put the answer in the id. Here the flag is no more the
# answer than it is there, and the reason for the name is different: nothing is
# wrong with the shop at all, so there is no condition of the shop's to name.
MONITORING_BLIND_SPOT = "monitoring-blind-spot"
# Named for the event rather than for the state, which is the opposite choice to
# `silent-data-corruption` beside it and made for the same reason. There the
# state is named because a flag caused it and naming the cause would put the
# answer in the id. Here the cause is the whole of what has to be worked out -
# the figures are wrong, and whether that is a bad write, a bad read or a store
# that fell behind is the question - so the id names the failover, which is the
# one thing about this incident the shop's operator would have known at the time
# and nobody investigating the page can see.
STATE_DIVERGENCE = "cache-failed-over"

SCENARIOS: dict[str, Scenario] = {
    FEATURE_FLAG_TOGGLE: Scenario(
        id=FEATURE_FLAG_TOGGLE,
        title="Feature flag toggled on",
        description=(
            "Io's account page is getting a new feature: average spend per item "
            "this month. It ships behind a feature flag, "
            "'monthly-spend-feature'. With the flag on, the feature is live - "
            "this month's total spend divided by the number of items bought "
            "this month. The rollout is a canary: 40% of account pages get it. "
            "But most shoppers have bought nothing this month, so that divisor "
            "is zero and those pages fail. Turning the flag back off ends it. "
            "The error rate settles at roughly a third, while latency stays "
            "flat."
        ),
        family=FLAG_CHANGES,
    ),
    FALLBACK_DISABLED: Scenario(
        id=FALLBACK_DISABLED,
        title="Fallback flag switched off",
        description=(
            "The same monthly-spend bug, guarded the other way round. "
            "'legacy-checkout-fallback' keeps account pages on the old, safe "
            "renderer, and it has been on for months - nobody thinks of it as a "
            "change. Switching it off exposes the monthly-spend path to the "
            "canary's traffic, and pages start failing for every shopper who "
            "has bought nothing this month. The incident began when a flag was "
            "turned *off*, so an agent that can only turn flags off cannot end "
            "it: the fix is to switch this one back on."
        ),
        family=FLAG_CHANGES,
        flag_role=FALLBACK_FLAG,
        breaks_when_flag_is_on=False,
        offered_in_console=False,
    ),
    FLAG_TOGGLE_RED_HERRING: Scenario(
        id=FLAG_TOGGLE_RED_HERRING,
        title="Innocent feature flag toggle",
        description=(
            "'monthly-spend-feature' really was switched on, the logs really do "
            "show it, and it really is not what is breaking the shop - a "
            "coincidence, which is what most correlated changes in a real "
            "incident turn out to be. Turning the flag back off changes "
            "nothing, so a mitigation taken on it is refuted rather than "
            "confirmed, and the flag has to be put back where it was found."
        ),
        family=FLAG_CHANGES,
        recovers_when_flag_reverts=False,
    ),
    COMPETING_FLAG_CHANGES: Scenario(
        id=COMPETING_FLAG_CHANGES,
        title="Two flags changed at once",
        description=(
            "Two flags moved in the same minute, and both touch the account "
            "page. 'legacy-checkout-fallback' was switched off, exposing the "
            "monthly-spend path to the canary's traffic - that is what is "
            "breaking the shop. 'monthly-spend-feature' was switched on in the "
            "same minute by someone else entirely, and is a coincidence. The "
            "evidence supports both readings, so the first thing tried may well "
            "be the wrong one: reverting the feature flag changes nothing and "
            "has to be undone, and only switching the fallback back on ends the "
            "incident."
        ),
        family=FLAG_CHANGES,
        flag_role=FALLBACK_FLAG,
        breaks_when_flag_is_on=False,
        decoy_flag_role=FEATURE_FLAG,
    ),
    RESOURCE_LEAK: Scenario(
        id=RESOURCE_LEAK,
        title="The shop is leaking memory",
        description=(
            "Io's account page remembers every shopper who visits it, so the "
            "page can greet them with what they saw last time. Nothing ever "
            "drops an entry, so the heap climbs for as long as the process is "
            "up. Memory departs its baseline first, latency follows it once "
            "the collector stops keeping up, and the error rate only moves at "
            "the very end when allocations start failing - which is the order "
            "that makes a leak so easy to page on too late. No flag touches "
            "it. Restarting the shop reclaims the heap and the climb begins "
            "again, because the fault is still in the code: the incident is "
            "mitigated, never resolved, and what ends it is a fix."
        ),
        family=CAPACITY,
        leaks=True,
    ),
    UPSTREAM_DEPENDENCY_FAILURE: Scenario(
        id=UPSTREAM_DEPENDENCY_FAILURE,
        title="The payment provider is down",
        description=(
            "Io's account page shows the card a shopper will be charged with, "
            "and Io does not hold that card - the payment provider does, and "
            "the page asks for it while it renders. The provider starts "
            "refusing, so every account page waits on it and then fails. The "
            "error rate and the latency move together, memory stays where it "
            "was, no flag was touched and nothing was deployed. Nothing Argus "
            "may do reaches it: the flag is not the problem, the process is "
            "not the problem, and a restart returns a shop that still cannot "
            "reach the provider. The correct outcome is that Argus says what "
            "happened and escalates it to somebody who can call them."
        ),
        family=NEIGHBOURS,
        upstream_fails=True,
    ),
    CACHE_MISCONFIGURED: Scenario(
        id=CACHE_MISCONFIGURED,
        title="The cache is on the wrong port",
        description=(
            "Io's account page reads a shopper's spend figure from a cache "
            "before working it out, because working it out means walking their "
            "whole purchase history. A configuration change moved the cache's "
            "port in 'deploy/values-production.yaml', so the shop now dials an "
            "address nothing is listening on. The cache itself is up and well. "
            "Every lookup fails to connect, every page recomputes, and every "
            "page is still correct - the fallback is the designed behaviour, "
            "so the error rate never moves and nobody is paged for a failure. "
            "What moves is the median: nine requests in ten used to be served "
            "from cache, and now none are. The tail barely stirs, because the "
            "slowest one in twenty was always a recomputed page - which makes "
            "this the one incident a monitor watching p95 cannot see. Nothing "
            "in the source changed, no flag was touched, and a restart brings "
            "back a shop reading the same configuration. What ends it is "
            "rolling the deployment back to the revision before the port moved."
        ),
        family=CONFIGURATION,
        cache_is_misconfigured=True,
        deploy=ScenarioDeploy(
            revision=THE_COMMIT_THAT_MOVED_THE_CACHE_PORT,
            previous_revision=THE_COMMIT_BEFORE_IT,
            repo_url="https://github.com/ohadraz/Argus-Demo-Target-App",
            path="deploy",
            initiated_by="kuki",
        ),
    ),
    SLOW_CANARY_ROLLOUT: Scenario(
        id=SLOW_CANARY_ROLLOUT,
        title="A slow feature, out to a few percent",
        description=(
            "Io's account page is getting a third figure: the shopper's "
            "typical purchase - the middle of their history rather than the "
            "average, so one expensive buy stops distorting it. It ships "
            "behind 'monthly-spend-feature' at three percent of traffic. The "
            "figure is right every time. What is wrong is how it is worked "
            "out: the code takes the cheapest purchase that is left, over and "
            "over, until the middle remains, so it walks the history once per "
            "item and a shopper who has bought a dozen things waits more than "
            "two seconds for their page. Nothing fails, so the error rate "
            "never moves. Three requests in a hundred is below the 95th "
            "percentile by arithmetic, so the tail a monitoring stack watches "
            "does not move either - and neither does the median, because "
            "ninety-seven requests in a hundred are served exactly as they "
            "were. The only place this incident exists is the 99th percentile, "
            "where it is ten times its baseline. It is the mirror of the cache "
            "scenario: that one hides in the tail, this one hides behind it. "
            "Turning the flag back off ends it, and ends it completely - "
            "there is nothing left in a heap or in a file for anything to "
            "bring back."
        ),
        family=THE_TAIL,
        rollout_is_slow=True,
    ),
    MONTHLY_STATEMENT_PANEL: Scenario(
        id=MONTHLY_STATEMENT_PANEL,
        title="Monthly statement panel switched on",
        description=(
            "The account page is getting a whole panel rather than a figure: "
            "this month laid out - what was spent, across how many purchases, "
            "in what sizes, by category, against the shopper's usual month. It "
            "ships behind 'monthly-spend-feature' to the same canary the "
            "monthly average went out to, and it breaks for the same shoppers, "
            "because a month with nothing bought in it has no largest purchase "
            "any more than it has an average. Turning the flag back off ends "
            "it. Everything a reader of the telemetry can see is the same as "
            "the monthly-summary incident; what differs is where the fault "
            "lives. The statement is the largest module in the shop, so the "
            "permanent fix is a large one - which is the only thing this "
            "scenario is here to stage, and the reason it is not offered "
            "alongside the others."
        ),
        family=RELEASES,
        ships_the_statement=True,
        offered_in_console=False,
    ),
    BAD_DEPLOYMENT: Scenario(
        id=BAD_DEPLOYMENT,
        title="Bad version deployed",
        description=(
            "A deployment lands and the shop gets slower - every page of it. "
            "The revision that went out derives a shopper's lifetime average "
            "from their purchases instead of the total the account already "
            "carries, and it does that once per purchase, so the figure is the "
            "one it always was and takes ten times as long to produce. This "
            "shop runs without a summary cache, so every request computes its "
            "own figure and every request pays: the median, the 95th and the "
            "99th percentile all climb together, which is what a deployment "
            "looks like and what no other scenario here stages. Nothing fails, "
            "so the error rate never moves. The deploy is recorded only in the "
            "Argo CD history - no log line mentions a release - so the only "
            "evidence naming a cause is the history, and what ends the "
            "incident is returning the deployment to the revision before it."
        ),
        family=RELEASES,
        deploy_is_slow=True,
        deploy=ScenarioDeploy(
            revision=THE_COMMIT_THAT_SLOWED_THE_AVERAGE,
            previous_revision=THE_COMMIT_BEFORE_THAT_ONE,
            repo_url="https://github.com/ohadraz/Argus-Demo-Target-App",
            path="deploy",
            initiated_by="kuki",
        ),
    ),
    PRICING_SERVICE_DEGRADED: Scenario(
        id=PRICING_SERVICE_DEGRADED,
        title="A service the shop forgot it calls",
        description=(
            "Io's account page shows the shopper what their basket comes to "
            "with their discounts applied, and Io does not work that figure "
            "out - 'io-pricing' does, a service the same company runs, and the "
            "page asks it while it renders. That service starts taking an "
            "order of magnitude longer to answer. It answers every call, so "
            "nothing fails and the error rate never moves; the shop simply "
            "waits, on every request, and the median, the 95th and the 99th "
            "all climb together. Memory is flat, no flag was touched and "
            "nothing was deployed - so the shape says 'a deployment' and the "
            "deploy history is empty. The only evidence naming a cause is the "
            "shop's own log: a WARN line saying which host the time went to. "
            "What that host is worth knowing about is in the service registry, "
            "which nobody reads until an incident: 'io-pricing' belongs to this "
            "company, and the payment provider beside it does not. Restarting "
            "the shop changes nothing, because nothing is wrong with the shop. "
            "What ends it is restarting a service Argus was not paged about."
        ),
        family=NEIGHBOURS,
        dependency_is_slow=True,
    ),
    CPU_SATURATION: Scenario(
        id=CPU_SATURATION,
        title="More shoppers than the shop was sized for",
        description=(
            "Nothing about Io is wrong. Its code, its configuration, its flags "
            "and its heap are all exactly where they were, and no deployment "
            "went out. What changed is how many shoppers arrived: the traffic "
            "climbs over ten minutes to four and a half times the volume Io's "
            "three replicas were sized for, and then stays there. Every request "
            "begins queueing for a core that is already busy, so the median, the "
            "95th and the 99th all climb together, while nothing fails and the "
            "error rate never moves. The shape says 'a deployment' and the "
            "deploy history is empty. What names the cause is the metrics "
            "themselves: the reported volume moved, and moved first, and CPU is "
            "pinned at the capacity three replicas have. Restarting changes "
            "nothing - demand and capacity are both where they were - and what "
            "ends it is making the shop bigger. Nothing ends the traffic, so "
            "this one is mitigated and never resolved: the values file still asks "
            "for three, and putting the count back returns the shop to "
            "saturation."
        ),
        family=CAPACITY,
        surges=True,
    ),
    AUTOSCALER_FLAPPING: Scenario(
        id=AUTOSCALER_FLAPPING,
        title="A shop that keeps changing size",
        description=(
            "The same shoppers as the scenario above, and one difference: this "
            "deployment has an autoscaler. Nothing about Io is wrong - its code, "
            "its configuration, its flags and its heap are all where they were, "
            "and no deployment went out. What is wrong is the controller. It "
            "scales up on a saturated minute, but the replicas it asked for are "
            "serving nothing until the minute after that, so the next minute is "
            "still saturated too; the one after it finally runs at six and "
            "reports a fraction of its CPU target, and the controller answers "
            "that by taking the replicas straight back. Its scale-down "
            "stabilisation window is zero, which is the field Kubernetes "
            "defaults to five minutes precisely so this cannot happen. So the "
            "shop is a different size every few minutes, latency never settles, and "
            "nothing fails - the error rate never moves. Both obvious answers "
            "are undone in front of you. Restarting changes nothing, because "
            "nothing is wrong with the process. Scaling out changes things for "
            "one minute, and then the controller puts the count back - which is "
            "what makes this the one incident where adding capacity is not the "
            "answer. What ends it is taking away the controller's room to "
            "shrink: raise its floor to its ceiling and the count stops moving. "
            "The values file still declares the window that flaps, so this is "
            "mitigated and never resolved, and putting the floor back returns "
            "the shop to flapping."
        ),
        family=CAPACITY,
        autoscaler_flaps=True,
    ),
    HALF_FINISHED_ROLLOUT: Scenario(
        id=HALF_FINISHED_ROLLOUT,
        title="A deployment that stopped half-way",
        description=(
            "A revision went out and its rolling update was paused half-way - "
            "somebody sent half the fleet to it to watch it, and went off "
            "shift. What that revision changed is the shape of what Io's "
            "summary cache stores: an entry used to be a figure, and now it is "
            "a figure and the purchases behind it. Three replicas write the "
            "new shape. Three replicas were deployed before it existed and "
            "cannot read it. So an account page fails when a replica on the "
            "older side draws an entry a replica on the newer side wrote, and "
            "that is the only thing that fails. The share it amounts to is a "
            "product of two shares - written by the new side, read by the old "
            "- so it is nothing before a rollout begins, nothing once it "
            "finishes, and largest exactly here: about one request in five, "
            "with the cache carrying its usual nine in ten. Every quantile "
            "stays flat, the cache is up and answering at the ratio it always "
            "did, no flag was touched, and the process has been up since "
            "before any of it. Both revisions pass the shop's own tests, so "
            "there is no bad commit to find and nothing for a patch to fix - "
            "what was skipped is a migration step, which is a process rather "
            "than a file. Restarting changes nothing: the fleet is still split "
            "when the process comes back. What ends it is returning the "
            "deployment to the revision before it, not because that revision "
            "was innocent but because one version reading and writing one "
            "shape is a shop that works, whichever version it is. The "
            "repository still declares the revision that was going out, so "
            "this is mitigated and never resolved."
        ),
        family=HALF_ROLLED_OUT,
        rollout_is_paused=True,
        deploy=ScenarioDeploy(
            revision=THE_COMMIT_THAT_RESHAPED_THE_CACHE_ENTRY,
            previous_revision=THE_COMMIT_BEFORE_THE_RESHAPE,
            repo_url="https://github.com/ohadraz/Argus-Demo-Target-App",
            path="deploy",
            initiated_by="kuki",
        ),
    ),
    SILENT_DATA_CORRUPTION: Scenario(
        id=SILENT_DATA_CORRUPTION,
        title="Totals that stopped keeping up",
        description=(
            "Io is making checkout cheaper. Recording a purchase used to re-add "
            "a shopper's whole history to work out what they had spent; behind "
            "'monthly-spend-feature' it simply adds the price to the totals the "
            "account already carries. The cheaper path adds it to the lifetime "
            "total and not to this month's, on the reasoning that a month can be "
            "added up from the purchases whenever anybody wants it - which is "
            "true of the lifetime average, and is not true of the monthly figure "
            "that reads the stored total. So every sale leaves that total a "
            "little further behind, and nothing anywhere notices. Every account "
            "page renders. The lifetime figure is right, because it derives its "
            "own total from the list of purchases; the monthly figure beside it "
            "is low, and the two numbers disagree on the same page. Nothing "
            "throws, so the error rate never moves. Nothing waits, so no "
            "quantile moves. The heap is flat, the shop is the right size, the "
            "cache is answering and the process has been up for hours. No rule "
            "fires and nobody is paged - which is the incident. What finds it is "
            "a job of Io's own, run weekly, that re-adds every shopper's "
            "purchases and counts the totals that disagree: it fires an alert "
            "carrying how many, the widest gap, and the oldest purchase that gap "
            "can be made of. That last figure is the only thing in the whole "
            "incident that says when the writing went wrong, because the job "
            "found it a week late. Turning the flag back off stops the next "
            "purchase being mis-recorded and repairs not one of the thousands "
            "already written, so the check run afterwards finds the same accounts "
            "and nothing newer - and a restart changes nothing at all, because "
            "the fault is in what was written down. What is left is a fix to the "
            "write path and a one-off repair of the data, and nobody may run the "
            "repair without being asked."
        ),
        family=FOUNDATIONAL_INTEGRITY,
        drifts_the_monthly_total=True,
    ),
    MONTHLY_TOTALS_FALLING_BEHIND: Scenario(
        id=MONTHLY_TOTALS_FALLING_BEHIND,
        title="Totals that stopped keeping up, shipped",
        description=(
            "The scenario above, with one difference. Nobody switched a flag: "
            "a revision went out that records a purchase without moving the "
            "shopper's monthly total at all, on the same reasoning - a month "
            "can be added up from the purchases whenever anybody wants it - and "
            "it is the revision every purchase has been written through since. "
            "Every account page renders, the lifetime figure is right and the "
            "monthly one beside it is low. No series moves, no rule fires and "
            "nobody is paged, until Io's weekly check re-adds every shopper's "
            "purchases and fires an alert carrying how many totals disagree, "
            "the widest gap, and the oldest purchase that gap can be made of. "
            "That purchase dates the incident, and what sits at that minute is "
            "a deployment in the Argo CD history - and nothing in the flag "
            "history at all. So putting a flag back answers nothing here, and "
            "a walk that reaches for one has learned the scenario above rather "
            "than the mode. Returning the deployment stops the next purchase "
            "being mis-recorded and repairs not one of those already written; "
            "a restart changes nothing. The revision sits on a branch of its "
            "own, so the shop's main branch still carries the fault behind the "
            "flag - and a fix to main's write path is the same fix."
        ),
        family=FOUNDATIONAL_INTEGRITY,
        drifts_the_monthly_total=True,
        deploy=ScenarioDeploy(
            revision=THE_COMMIT_THAT_STOPPED_CARRYING_THE_MONTH,
            previous_revision=THE_COMMIT_BEFORE_THE_MONTH_STOPPED_BEING_CARRIED,
            repo_url="https://github.com/ohadraz/Argus-Demo-Target-App",
            path="deploy",
            initiated_by="kuki"
        )
    ),
    CONTROL_PLANE_UNREACHABLE: Scenario(
        id=CONTROL_PLANE_UNREACHABLE,
        title="The platform that carries the fix will not act",
        description=(
            "Two changes reached Io's account page in the same minute, and the "
            "page started failing. A revision went out that reworks how a "
            "shopper's average is worked out, and 'monthly-spend-feature' was "
            "switched on for two in five account pages. The revision is the "
            "closer and more specific change, so returning it is the first "
            "thing worth trying - and it is the one thing that cannot be done, "
            "because the deployment platform's API server is not answering. "
            "Every generic mitigation but one goes through that platform: the "
            "rollback, the restart, the scale-out and the autoscaler pin are "
            "unavailable together, and none of them is unavailable for any "
            "reason to do with this incident. What is left is the flag, which "
            "lives with a different provider, is still answering, and does end "
            "the incident when it goes back. The shop itself is well "
            "throughout - it serves, it reports its own metrics, and the "
            "platform goes on saying what it has deployed. It simply will not "
            "act."
        ),
        family=FOUNDATIONAL_INTEGRITY,
        control_plane_is_down=True,
        deploy=ScenarioDeploy(
            revision=THE_COMMIT_THAT_MOVED_THE_MONTH_BOUNDARY,
            previous_revision=THE_COMMIT_BEFORE_THE_MONTH_BOUNDARY_MOVED,
            repo_url="https://github.com/ohadraz/Argus-Demo-Target-App",
            path="deploy",
            initiated_by="kuki",
        ),
    ),
    STATE_DIVERGENCE: Scenario(
        id=STATE_DIVERGENCE,
        title="A cache serving figures the ledger has moved past",
        description=(
            "Io's account page reads a cache before it works out what a shopper "
            "has spent this month, and recomputes from the purchase ledger "
            "whenever the cache has nothing for them. That cache lost its "
            "primary, and the standby promoted in its place had stopped "
            "receiving updates three hours before the promotion. So it now "
            "serves, as current, every figure it was holding when replication "
            "broke. A shopper who has bought anything since reads a monthly "
            "total that stops before those purchases - printed on the same page "
            "as the purchases themselves, which are read from the ledger and are "
            "correct. Nothing is wrong with the ledger, nothing is wrong with "
            "the code, and no change went out: no flag moved, no revision "
            "deployed, no process restarted. Every page returns 200. Nothing "
            "throws, so the error rate never moves; nothing waits, so no "
            "quantile moves; the cache is answering as fast as it ever did, "
            "because serving a stale figure costs exactly what serving a fresh "
            "one costs. The heap is flat, the shop is the right size and the "
            "process has been up for hours. No rule fires and nobody is paged. "
            "What finds it is a job of Io's own, which re-adds every shopper's "
            "purchases and compares the result against what the cache holds: it "
            "fires an alert carrying how many entries disagree out of how many "
            "it checked, the widest gap, the keys of the entries themselves, and "
            "two instants - the promotion, which is when the shop started "
            "serving wrong figures, and the oldest purchase no stale figure "
            "accounts for, which is when replication broke three hours earlier. "
            "The count climbs while the incident runs, because an entry goes "
            "stale the moment its shopper buys something. Discarding the named "
            "entries is a complete fix for them: the cache holds nothing for "
            "those shoppers, so their pages work the figure out from the ledger "
            "and are right. It does not touch the promoted standby, which is "
            "still the cache being served, or the replication that let a lagging "
            "replica be promoted - so the condition can produce the same "
            "incident again, and what is owed at the end is a change to how the "
            "cache fails over."
        ),
        family=FOUNDATIONAL_INTEGRITY,
        cache_failed_over=True,
    ),
    MONITORING_BLIND_SPOT: Scenario(
        id=MONITORING_BLIND_SPOT,
        title="The shop stops reporting and goes on trading",
        description=(
            "A deployment renamed the shop's metrics port in "
            "'deploy/values-production.yaml' - 'metrics' became 'http-metrics', "
            "so that every port in the file is named for the protocol it "
            "carries. Nothing in the shop reads that name. The scrape config "
            "does, and it selects the target by it, so from the minute the "
            "revision landed the platform stopped collecting from a shop that "
            "was still serving its metrics endpoint exactly as before. The shop "
            "goes on trading as it was - orders succeed, every account page "
            "renders the right numbers, nothing throws and nothing waits - and "
            "`/metrics` carries no row at all. The window before it is ordinary "
            "and the window after it does not exist. Nothing crossed a "
            "threshold, because for those minutes there is no series to cross "
            "one. What pages somebody is the rule every monitoring stack has "
            "and none of these scenarios has needed yet: a series that was "
            "reporting has stopped, held long enough that it cannot be a scrape "
            "that was missed. The alert says when the last sample arrived, "
            "which is the only thing that dates the incident, and the shop's "
            "logs go on saying it is well across every minute the metrics do "
            "not cover - which is the corroboration, and the only channel that "
            "has any. No flag moved, so there is nothing to revert; a restart "
            "changes nothing, because the renamed port comes back with the "
            "process. What ends it is rolling the deployment back to the "
            "revision before the rename, which restores the collecting from "
            "that minute on and restores not one of the minutes it was blind "
            "for: they were never collected and nothing keeps them."
        ),
        family=FOUNDATIONAL_INTEGRITY,
        stops_publishing_telemetry=True,
        deploy=ScenarioDeploy(
            revision=THE_COMMIT_THAT_RENAMED_THE_METRICS_PORT,
            previous_revision=THE_COMMIT_BEFORE_THE_RENAME,
            repo_url="https://github.com/ohadraz/Argus-Demo-Target-App",
            path="deploy",
            initiated_by="kuki"
        )
    )
}


def scenario_span_minutes(scenario: Scenario) -> int:
    return max(entry.offset_minutes for entry in scenario.minutes)


def bucket_id(seeded_at: datetime, offset_minutes: int, span_minutes: int) -> str:
    """Format one authored scenario minute as a bucket id, anchored so the
    scenario's *last* minute is the seed instant.

    The incident has therefore already happened by the time anything asks about
    it, which is the only way round it can be for an authored scenario: a
    consumer windowing its retrieval will end that window at "now", because no
    minute after now exists to be read. Anchoring the scenario's *first* minute
    at the seed instant would put the rest of the incident in the future, where
    a correct reader cannot see it.

    This is also exactly why an authored scenario cannot express recovery, and
    why the flag scenario is generated instead.

    The same string prefixes that minute's log lines, so a caller can match a
    bucket to its entries without re-parsing either.
    """
    minute = seeded_at.replace(second=0, microsecond=0) + timedelta(
        minutes=offset_minutes - span_minutes
    )
    return minute.strftime(TIMESTAMP_FORMAT)


def utc_now() -> datetime:
    return datetime.now(UTC)
