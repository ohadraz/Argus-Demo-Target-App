from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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

    `offered_in_console` is presentation only. A scenario kept for the capability
    it pins down is not automatically one worth showing an audience; hiding it
    leaves it seedable by id, which is how the e2e suite stages it.
    """

    id: str
    title: str
    description: str
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
    ships_the_statement: bool = False
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
        revision that is running, and a slow dependency is another team's
        process. A page offering a flag to watch for any of them would be
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
        )

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
THE_COMMIT_THAT_MOVED_THE_CACHE_PORT = "0d8e826225f0de73958a8a8dd3d867b2ae249e72"
THE_COMMIT_BEFORE_IT = "544cef36a8eaf45c5b030c3d5c21473d8176cef3"
# The revision the bad deployment shipped: the lifetime average derived from
# the purchases once per purchase. A literal for the reason the cache port's
# commit is one - the commit cannot name itself.
THE_COMMIT_THAT_SLOWED_THE_AVERAGE = (
    "5e07d73148d0a704b8fefe5f379bc652bb773655"
)
THE_COMMIT_BEFORE_THAT_ONE = "70dbcfde2b549d110a3817d92d60b6dd9786e78b"
FALLBACK_DISABLED = "fallback-disabled"
FLAG_TOGGLE_RED_HERRING = "flag-toggle-red-herring"
COMPETING_FLAG_CHANGES = "competing-flag-changes"
SLOW_CANARY_ROLLOUT = "slow-canary-rollout"
MONTHLY_STATEMENT_PANEL = "monthly-statement-panel"
PRICING_SERVICE_DEGRADED = "pricing-service-degraded"

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
        dependency_is_slow=True,
    ),
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
