"""What is currently staged, and keeping it honest against the live flag.

In-memory only: a restart clears it. The scenario definitions are code, not
state, so they survive restarts unaffected.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from io_shop.cache_reconciliation import (
    CacheReconciliation,
    reconcile_cached_summaries
)
from io_shop.spend_reconciliation import Reconciliation
from io_shop.summary_cache import CacheEndpoint
from io_shop.visits import forget_every_visit
from target_app.cache_entries import (
    a_client_for,
    discard_every_entry,
    forget_every_cached_summary,
    write_entries
)
from target_app.flags import FlagClient, FlagProviderUnavailable
from target_app.generator import (
    SETTLED_UPTIME,
    CacheOutage,
    Capacity,
    DemandSurge,
    FlagTimeline,
    LiveAutoscaler,
    PausedRollout,
    Pin,
    PricingSlowdown,
    ProviderOutage,
    Scaling,
    ScrapeOutage,
    SlowDeployment,
)
from target_app.history import (
    forget_the_changes_to,
    record_the_change_as_having_happened_at,
)
from target_app.integrity import (
    the_accounts_the_check_examines,
    the_figures_the_promoted_standby_holds,
    what_the_check_found
)
from target_app.scenarios import (
    FALLBACK_FLAG,
    FEATURE_FLAG,
    Scenario,
    description_for,
    quiet_state_for,
    utc_now,
)
from target_app.settings import (
    get_scenario_settings,
    the_declared_autoscaler,
    the_deployed_cache_endpoint,
    the_deployed_replica_count,
    the_working_cache_endpoint,
)

# The phases a scenario passes through, as anything watching them sees it.
# `RUNNING` and `RECOVERING` are the ones during which something is happening
# that an interruption would spoil; `COMPLETE` says the window has stopped
# advancing and the next scenario can be staged.
IDLE = "idle"
STAGED = "staged"
RUNNING = "running"
RECOVERING = "recovering"
COMPLETE = "complete"

# How many clean whole minutes a scenario keeps generating past its recovery
# before the window stops advancing.
#
# Three because two is the answer with no margin. Argus confirms a mitigation from
# a run of clear minutes reaching its own `anomaly_persistence_minutes` - two by
# default, and in the other repo - and a window frozen with fewer than that
# refutes every mitigation in every scenario, looking like a broken detector
# rather than a fixture that is wrong. It was one once, and one bad minute then
# refuted a mitigation that had worked.
CLEAN_MINUTES_SHOWN_AFTER_RECOVERY = 3


def _settled_at(turned_off_at: datetime) -> datetime:
    """When the window stops advancing, having shown the recovery hold.

    Counted from the first *whole* minute after the revert rather than from the
    revert itself. A revert lands mid-minute, so the minute it happened in is
    part broken and part clean, and a settling period measured from the instant
    ends mid-minute too - which leaves the first clean bucket partial, and
    leaves it missing altogether when the revert lands on a minute boundary,
    since a minute with no elapsed seconds is no reading rather than a quiet
    one. That bucket is the one a mitigation reads its verdict off, and a window
    that sometimes omits it refutes an action that worked.
    """
    first_clean_minute = (
        turned_off_at.replace(second=0, microsecond=0) + timedelta(minutes=1)
    )

    return first_clean_minute + timedelta(minutes=CLEAN_MINUTES_SHOWN_AFTER_RECOVERY)


def _has_fallen_behind(timeline: FlagTimeline, moved_now: bool) -> bool:
    """Whether this timeline still describes where its flag actually sits.

    A timeline recording no end says the flag is away from its quiet state; one
    recording an end says it is back. So the two agree exactly when they
    disagree as booleans, and a timeline has fallen behind when they read alike.
    """
    return moved_now is (timeline.turned_off_at is not None)


def _caught_up_with_the_flag(timeline: FlagTimeline,
                             moved_now: bool) -> FlagTimeline:
    """One timeline caught up with its flag, in whichever direction it moved.

    Both directions, because an agent that moves a flag may put it back: every
    mitigation Argus takes is undone when it fails to help, so a flag switched at
    one reading is switched back at a later one. Read in one direction only -
    which is how both of these were written - a timeline froze at the first
    revert, and the shop went on reporting the flag where it no longer was. That
    is the fixture lying in the one channel an agent checks to find out what its
    own action did.

    Reopening starts a fresh stretch rather than extending the old one. A
    timeline holds one on-and-off pair, so the minutes between a revert and a
    re-enable cannot be expressed as a gap - and claiming the flag was away
    throughout would erase the evidence that the revert worked, which is the one
    thing those minutes are read for. What is lost instead is the earlier
    stretch's dates: minutes before the re-enable read as quiet. That is the
    right way round for a fixture, whose job is to be honest about the state an
    agent is about to act on.
    """
    if moved_now:
        return FlagTimeline(turned_on_at=utc_now())

    return replace(timeline, turned_off_at=utc_now())


def _the_decoys_quiet_state(scenario: Scenario) -> bool:
    """The state the decoy sits in when nothing is going on.

    The same rule every flag follows, applied to the decoy's own role. The
    decoy is then moved *away* from it, which is what makes it look, to anyone
    reading what changed, exactly like the change that broke the shop.
    """
    return quiet_state_for(scenario.decoy_flag_role)


# Clearing the flag provider's recorded history, injected so that a test can
# watch a reset ask for it without a provider database to ask.
HistoryEraser = Callable[[Sequence[str]], None]

# And moving a recorded change back to when it happened, injected for the same
# reason. Its own seam rather than a flag on the eraser's: a reset erases and a
# staging backdates, and the two are asked for at opposite ends of a run.
HistoryBackdater = Callable[[Sequence[str], datetime, datetime], None]


def _how_long_ago_this_one_began(scenario: Scenario) -> timedelta:
    """How far back a scenario's onset is placed from the instant it is staged.

    Minutes for every scenario whose incident is visible in a series: enough
    history for an onset to be located in, and no more, so an audience is not
    watching a flat graph waiting for something to happen.

    Days for the one whose incident is visible in no series at all. Its onset has
    to be *older than the metrics reach*, because the whole claim of that scenario
    is that nothing in the telemetry dates the fault and only the data does - and
    an onset a consumer could measure for itself would leave that claim untested.
    """
    settings = get_scenario_settings()

    if scenario.drifts_the_monthly_total:
        return timedelta(days=settings.drift_backdate_days)

    return timedelta(minutes=settings.onset_backdate_minutes)


def _set(client: FlagClient, enabled: bool) -> None:
    if enabled:
        client.enable()
    else:
        client.disable()


@dataclass(frozen=True)
class FlagMoment:
    """One flag change this service noticed after the scenario was staged.

    Everything a watcher is trying to follow is here: when, which flag, and
    which way. Staging's own changes are not moments - they are the incident,
    not an answer to it - so the list holds exactly what somebody *did about*
    the incident, in the order they did it.

    Noticed rather than reported: nobody tells this service that a flag moved,
    and the whole point of the demo is that anybody may move one.
    """

    at: datetime
    flag: str
    enabled: bool


@dataclass(frozen=True)
class ActiveScenario:
    """The scenario now running, and whatever that scenario needs to serve.

    `seeded_at` anchors an authored scenario's minutes. `timeline` carries a
    generated scenario's flag history. Each is `None` for the kind that does
    not use it, rather than being given a meaningless default - a seed instant
    on a scenario nothing anchors to would be a number waiting to be believed.
    """

    scenario: Scenario
    seeded_at: datetime | None = None
    # When the shop's monitoring paged somebody about this, which is a
    # different moment from when it broke and the only one a responder could
    # have acted on. `None` until an alert is actually fired: a scenario staged
    # and never alerted on is an incident nobody was paged for, and the on-call
    # provider holds nothing about it.
    alerted_at: datetime | None = None
    timeline: FlagTimeline | None = None
    # The decoy's own history, for a scenario that stages one. Separate from
    # `timeline` because the two diverge the moment somebody reverts the decoy:
    # that revert is real and belongs in the logs, and it ends nothing.
    decoy_timeline: FlagTimeline | None = None
    # When the serving process came up, as every metric bucket reports it.
    # Fixed at staging rather than recomputed per read: a start time derived
    # from the current minute would move every minute, and a reader comparing
    # two polls would see a shop restarting itself continuously. `None` only
    # before anything is staged, where there is no telemetry to report it on.
    process_started_at: datetime | None = None
    # When the shop began retaining what it should have let go. `None` for
    # every scenario that is not about memory, which is what keeps the heap
    # flat in all of them - a fixture that moved every signal at once would
    # leave a reader unable to say which one the incident is about.
    leak_started_at: datetime | None = None
    # The stretch the payment provider spent refusing, for the one scenario
    # whose condition is not Io's to change. `None` everywhere else, which is
    # what keeps the provider answering in every other scenario.
    provider_outage: ProviderOutage | None = None
    # The stretch the shop spent unable to reach its summary cache, and the
    # address it was dialling over that stretch. `None` everywhere else, which
    # is what keeps every other scenario's shop without a cache at all rather
    # than with one that happens to be working - a scenario reporting a hit
    # ratio it never staged would be a fixture volunteering a signal.
    #
    # The address is carried beside the outage because it is the diagnosis: the
    # port here is the one the deployed revision configured, and setting it
    # against the previous revision's is what names the change. That is also
    # why it is state rather than the values file the shop ships - git holds
    # what was asked for, this holds what is actually running, and a rollback
    # is precisely the act of making the second agree with an earlier first.
    cache_outage: CacheOutage | None = None
    cache_endpoint: CacheEndpoint | None = None
    # When a standby that had stopped receiving updates was promoted in front of
    # shoppers, for the one scenario whose condition is what the cache holds
    # rather than whether it answers. `None` everywhere else.
    #
    # The moment the shop began serving stale figures, and deliberately not the
    # moment they froze - the entries stopped keeping up when replication broke,
    # which is earlier and is the figure the stale share derives from. Two
    # instants, because the incident's start and the fault's start are genuinely
    # different here, and an alert carrying only one of them would leave a
    # reader unable to say which.
    promoted_at: datetime | None = None
    # The stretch the platform was not collecting the shop's metrics over,
    # for the one scenario whose condition is that there is no reading rather
    # than that a reading moved. `None` everywhere else, which leaves every
    # other scenario publishing every minute it generates.
    scrape_outage: ScrapeOutage | None = None
    # The stretch the slower revision has been the one deployed, for the one
    # scenario whose condition is which revision is running. `None` everywhere
    # else, which leaves every other scenario's latency exactly where it was -
    # the multiplier this becomes is 1.0 in its absence.
    #
    # Stored rather than derived from the deploy history, because the history
    # records that a revision went out and this records over which minutes
    # running it cost anything. A rollback ends the stretch and leaves the
    # entry, which is what the history is for.
    deploy_slowdown: SlowDeployment | None = None
    # The stretch the revision that stopped carrying the month has been the one
    # deployed, for the one scenario whose drift a deployment shipped. `None`
    # everywhere else - the flag scenario's drift is read off the flag's own
    # timeline, and every other shop keeps its totals in step.
    #
    # In the flag's shape because the integrity check asks a write path's
    # stretch two things only - when it went live and when it stopped - and
    # asks them the same way whichever change it was. A rollback sets the end.
    drifting_revision: FlagTimeline | None = None
    # The stretch the fleet has spent split across two revisions, for the one
    # scenario whose condition is that a deployment did not finish. `None`
    # everywhere else, which is what keeps every other scenario's fleet on one
    # revision - and keeps the platform reporting a deployment that converged,
    # which is evidence too.
    #
    # Stored rather than derived from the deploy history, for the reason the slow
    # deployment's stretch is: the history records that a revision went out, and
    # this records over which minutes it had not finished going out. That is the
    # fact the history cannot hold at all, because a history is a list of
    # instants and this is a stretch.
    paused_rollout: PausedRollout | None = None
    # The stretch the pricing service has spent answering slowly, for the one
    # scenario whose condition belongs to a neighbour of Io's own. `None`
    # everywhere else, which is what keeps that service prompt - and silent - in
    # every other scenario.
    pricing_slowdown: PricingSlowdown | None = None
    # When the traffic began climbing, for the one scenario whose condition is how
    # much of it there is. `None` everywhere else, which leaves every other
    # scenario serving the baseline volume it always served.
    #
    # It has no end, and that is the one thing about it worth reading twice.
    # Every other condition here is a stretch something can bring to a close;
    # nothing Argus does to a deployment makes shoppers stop arriving, which is
    # why this mode is answered by adding capacity rather than by putting anything
    # back.
    demand_surge: DemandSurge | None = None
    # When the pricing service's own process came up. `None` until somebody
    # restarts it, and then the moment they did: the two services come up
    # together and diverge only when one of them is restarted, which is exactly
    # what `pricing_serving_since` says.
    #
    # A single instant rather than a list, unlike `restarts` below. Nothing
    # accumulates in that process as far as this fixture is concerned, so no
    # window has to remember where its restarts fell - all anybody asks of it is
    # whether the process serving now is a new one.
    pricing_started_at: datetime | None = None
    # Every time somebody has brought the process back since. A list rather
    # than a latest value, because a restart has to stay in the window it
    # happened in: the minutes before it kept the heap they had, and a single
    # moving instant would flatten the climb retrospectively and take the
    # incident out of the record the moment it was mitigated.
    restarts: tuple[datetime, ...] = ()

    @property
    def serving_since(self) -> datetime | None:
        """When the process now serving the shop came up.

        The latest restart if anybody has performed one, and otherwise when the
        scenario staged the process. `restarts` is kept as a list because the
        telemetry needs to know which minute each one fell in; this is the one
        question that only wants the last of them, which is whether the process
        answering right now is a new one.
        """
        if self.restarts:
            return self.restarts[-1]

        return self.process_started_at

    @property
    def pricing_serving_since(self) -> datetime | None:
        """When the process now answering as the pricing service came up.

        The instant the scenario staged until somebody restarts the pricing
        service, because the two applications are deployed together and have been
        up as long as each other. Derived rather than stored in every branch of
        `seed`: one fact said once, and a copy written into six scenarios is a
        copy that comes to disagree with the one that matters.

        `process_started_at` rather than `serving_since` above, and that is the
        whole point of there being two of these: restarting the shop moves the
        shop's answer and must leave this one exactly where it was.
        """
        if self.pricing_started_at is not None:
            return self.pricing_started_at

        return self.process_started_at


class ScenarioState:
    """Holds the active scenario, and reconciles it with the flag on every read.

    The reconciliation is the point. A generated scenario ends when its flag
    goes off, and this service is not told when that happens - the flag lives
    in a provider anyone can reach, and the whole design rests on *anyone*
    being able to end the incident. So every read asks the provider what the
    flag is now, and records the moment it first sees it off.

    That makes the recorded end time late by however long it has been since the
    last read, which in practice is under a second: whoever turned the flag off
    is about to look at the metrics to see whether it worked, and looking is
    what stamps it.
    """

    def __init__(
        self,
        flags: FlagClient,
        fallback_flags: FlagClient,
        forget_the_flag_history: HistoryEraser = forget_the_changes_to,
        backdate_the_flag_history: HistoryBackdater = (
            record_the_change_as_having_happened_at
        ),
    ) -> None:
        self._flags = flags
        self._fallback_flags = fallback_flags
        self._forget_the_flag_history = forget_the_flag_history
        self._backdate_the_flag_history = backdate_the_flag_history
        self._active: ActiveScenario | None = None
        self._moments: list[FlagMoment] = []
        self._last_seen: dict[str, bool] = {}
        # A GitOps deployment reconciles itself unless somebody has stopped it,
        # so this starts on. See `syncs_itself`.
        self._syncs_itself = True
        # Every resize the deployment has had, oldest first. A history rather
        # than a count, for the reason the process's restarts are one: see
        # `capacity`.
        self._scalings: tuple[Scaling, ...] = ()
        # Every raise of the autoscaler's floor, oldest first, for the reason the
        # resizes are a history: see `autoscaler`. Process state rather than the
        # active scenario's, exactly as the resizes are - it describes what has been
        # done to the deployment, and a pin outlives the incident that prompted it.
        self._pins: tuple[Pin, ...] = ()

    def _flags_for(self, scenario: Scenario) -> FlagClient:
        return (
            self._fallback_flags
            if scenario.flag_role == FALLBACK_FLAG
            else self._flags
        )

    def _decoy_flags_for(self, scenario: Scenario) -> FlagClient | None:
        """The client for the scenario's decoy flag, if it stages one.

        The decoy is always the flag the staged one is not - there are two in
        this shop, and a decoy that was the same flag would be the change it is
        supposed to be mistaken for.
        """
        if scenario.decoy_flag_role is None:
            return None

        return (
            self._fallback_flags
            if scenario.decoy_flag_role == FALLBACK_FLAG
            else self._flags
        )

    @property
    def active(self) -> ActiveScenario | None:
        return self._active

    @property
    def active_scenario_id(self) -> str | None:
        return self._active.scenario.id if self._active else None

    def somebody_was_paged(self) -> None:
        """Records that the monitoring has just fired an alert about this.

        The moment person-minutes are counted from. Nobody can respond to an
        incident before they are told about it, so the on-call provider's
        acknowledgements are placed relative to this rather than to the minute
        the shop actually broke - which is usually several minutes earlier and
        was, by definition, unattended.

        The first page stands. A scenario alerted on twice is the same incident
        reported twice, and moving the clock forward would shorten everybody's
        night retrospectively.
        """
        if self._active is None or self._active.alerted_at is not None:
            return

        self._active = replace(self._active, alerted_at=utc_now())

    def seed(self, scenario: Scenario) -> None:
        """Stages a scenario, establishing whatever live condition it needs.

        Starts by putting the shop back together, so staging always begins from
        a calm world. Here rather than in the console, because the button is not
        the only way in - the e2e suite seeds by id straight through the API -
        and a guard the page performs is a guard every other caller skips.

        The residue this clears is usually not the previous scenario's *staged*
        flag, which is why it is easy to miss: the ambiguous scenario can finish
        with its decoy still switched on, having been reverted and put back by
        an agent that found it innocent. Staging the deployment scenario next
        touches no flags at all, so nothing would correct it, and the
        investigation would open on a shop carrying a flag change from an
        incident that is over.

        Free where there is nothing to clear: the provider records a toggle only
        where something moved, so a seed onto an already-calm shop adds nothing
        to the history that the next investigation reads - and it makes the
        healthy-state-first step below a no-op, leaving the change that stages
        the incident as the only one recorded.
        """
        self._put_the_flags_back_where_they_rest()
        # After the clearing, not before: it can take a moment for the provider
        # to agree a flag has moved, and an onset anchored ahead of that would
        # backdate the incident to before the world it starts from.
        now = utc_now()

        if not scenario.is_generated:
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
            )
            return

        if scenario.upstream_fails:
            # No flag is touched, because no flag is involved, and no process
            # is either: what is wrong is another company's service, and the
            # only thing staged here is the moment it stopped answering.
            # Backdated like a flag's onset, so a diagnosable incident exists
            # the instant this returns.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                provider_outage=ProviderOutage(
                    began_at=now - timedelta(
                        minutes=get_scenario_settings().onset_backdate_minutes
                    )
                ),
            )
            return

        if scenario.stops_publishing_telemetry:
            # No flag, and nothing wrong with the shop at all - which is what
            # separates this from every other deployed-configuration scenario
            # here. The revision changed the *name* of the metrics port, so the
            # platform stopped selecting the shop as a scrape target while the
            # shop went on serving that endpoint to nobody. Nothing it does
            # differs; what differs is that no one is writing any of it down.
            #
            # Backdated like the others, so the incident is diagnosable the
            # instant this returns - and here the backdating is what makes it
            # an incident at all, since an absence rule fires on a gap held
            # long enough that it cannot be one collection that went astray.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                scrape_outage=ScrapeOutage(
                    began_at=now - timedelta(
                        minutes=get_scenario_settings().onset_backdate_minutes
                    )
                ),
            )
            return

        if scenario.cache_is_misconfigured:
            # No flag, no process, and nothing wrong with the cache either -
            # it is up and answering whoever dials it correctly. What is staged
            # is the deployed configuration being applied: the shop starts
            # dialling the port the values file at this revision names, which
            # is not where the cache is. Backdated like the others, so the
            # incident is diagnosable the instant this returns.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                cache_endpoint=the_deployed_cache_endpoint(),
                cache_outage=CacheOutage(
                    began_at=now - timedelta(
                        minutes=get_scenario_settings().onset_backdate_minutes
                    )
                ),
            )
            return

        if scenario.cache_failed_over:
            # No flag, no process, no deployment, and nothing unreachable: the
            # cache is up, answering at the address the deployment names, and as
            # fast as it ever was. What is staged is what it *holds* - the
            # figures it had when replication broke, promoted in front of
            # shoppers when the primary was lost.
            #
            # The only scenario that writes to a real store, and the one place
            # that does the writing. Every other scenario's cache is arithmetic
            # in the generator, which is what keeps their windows, recordings and
            # graded fixes exactly where they were.
            #
            # Cleared before it is written so a restage is not staged on top of
            # the last one's entries. A failure to reach the cache is raised
            # rather than tolerated - unlike the reset, which tolerates it,
            # because a scenario staged with no stale entries and an alert
            # describing ninety of them is a fixture contradicting itself.
            self._remember_where_the_flags_are_now()

            with a_client_for(the_working_cache_endpoint()) as cache:
                discard_every_entry(cache)
                write_entries(cache, the_figures_the_promoted_standby_holds(now))

            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                cache_endpoint=the_working_cache_endpoint(),
                # When shoppers started reading stale figures, which is not when
                # the figures froze. Backdated like every other onset so the
                # incident is diagnosable the instant this returns; the entries
                # froze three hours before the check runs regardless, so the
                # share that disagrees does not move with this.
                promoted_at=now - timedelta(
                    minutes=get_scenario_settings().onset_backdate_minutes
                ),
            )
            return

        if scenario.deploy_is_slow:
            # No flag, no cache and nothing unreachable. What is staged is the
            # revision that is deployed: the one this scenario names computes the
            # figure every page shows the long way round, so every request pays
            # and no aggregate hides it. No cache is configured, which is what
            # makes "every request" true - a cached page would not compute the
            # figure at all.
            #
            # Backdated like the others, so the incident is diagnosable the
            # instant this returns.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                deploy_slowdown=SlowDeployment(
                    began_at=now - timedelta(
                        minutes=get_scenario_settings().onset_backdate_minutes
                    )
                ),
            )
            return

        if scenario.drifts_from_a_deployment:
            # The flag scenario's drift with no flag: the revision this scenario
            # names writes every purchase through the path that skips the month,
            # so the drift began when it landed and lasts until it is returned.
            #
            # Backdated as far as the flag scenario's, for the same reason - the
            # onset has to be older than the metrics reach - and the deploy
            # history reads its landing from here, so the entry and the oldest
            # affected purchase sit at the same instant. No flag history is
            # touched: a flag change at that minute would make this the flag
            # scenario with a deployment beside it.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                drifting_revision=FlagTimeline(
                    turned_on_at=now - _how_long_ago_this_one_began(scenario)
                ),
            )
            return

        if scenario.rollout_is_paused:
            # No flag, nothing unreachable, nothing wrong with this process and
            # nothing wrong with either revision. What is staged is a deployment
            # that landed and stopped: the revision this scenario names went out,
            # its rolling update was paused half-way, and the fleet has been split
            # across two versions ever since.
            #
            # A cache is configured and left working, as the canary scenario
            # configures one: the incident is about what two versions of the shop
            # put in it, so there has to be a cache for them to disagree in - and
            # it answers at its usual ratio throughout, which is the series that
            # tells this apart from the shop losing its cache altogether.
            #
            # Backdated like the others, so the incident is diagnosable the
            # instant this returns.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                cache_endpoint=the_working_cache_endpoint(),
                paused_rollout=PausedRollout(
                    began_at=now - timedelta(
                        minutes=get_scenario_settings().onset_backdate_minutes
                    )
                ),
            )
            return

        if scenario.dependency_is_slow:
            # No flag, no cache, no deploy, and nothing wrong with this process
            # at all. What is staged is a neighbour: the pricing service every
            # account page asks what the shopper's basket comes to starts taking
            # an order of magnitude longer to answer. It answers every call, so
            # nothing fails and the error rate never moves - the shop simply
            # waits, on every request, because every page shows a basket total.
            #
            # No cache is configured, for the reason the deployment scenario
            # configures none: the wait lands on every request whichever path the
            # figure took, and a hit ratio reported here would be a signal this
            # scenario never staged.
            #
            # Backdated like the others, so the incident is diagnosable the
            # instant this returns.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                pricing_slowdown=PricingSlowdown(
                    began_at=now - timedelta(
                        minutes=get_scenario_settings().onset_backdate_minutes
                    )
                ),
            )
            return

        if scenario.surges:
            # No flag, no cache, no deploy, no neighbour and nothing wrong with
            # this process either. What is staged is the traffic: shoppers arrive
            # in numbers the deployment was not sized for, and the shop queues.
            # Backdated further than the others because a surge ramps rather than
            # steps - see `surge_backdate_minutes` - so that the plateau, the climb
            # and the quiet minutes before it are all in the window at once.
            #
            # The capacity it meets is not recorded here. It is the deployment's
            # size, which is process state and belongs to nobody's scenario, and a
            # seed that captured it would stage an incident against a count that
            # stopped being true the moment anybody scaled.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                demand_surge=DemandSurge(
                    began_at=now - timedelta(
                        minutes=get_scenario_settings().surge_backdate_minutes
                    )
                ),
            )
            return

        if scenario.autoscaler_flaps:
            # The surge's traffic exactly, and a controller. Nothing else is
            # staged: no flag, no cache, no deploy, no neighbour, and nothing
            # wrong with this process - which is the point, because the shop is
            # the right size for this load half the time and the wrong size the
            # rest of it.
            #
            # The same `DemandSurge` and the same backdating the surge uses, so
            # the ramp, the plateau and the quiet minutes before it are all in the
            # window at once - and so the two scenarios differ in one thing rather
            # than in a figure each.
            #
            # The autoscaler is not recorded here. It is derived from the scenario
            # and the deployment's own declaration (`autoscaler`), for the reason
            # the capacity a surge meets is not recorded: it is the platform's
            # arrangement with the application rather than anybody's incident, and
            # a seed that captured it would stage an incident against bounds that
            # stopped being true the moment anybody pinned anything.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                demand_surge=DemandSurge(
                    began_at=now - timedelta(
                        minutes=get_scenario_settings().surge_backdate_minutes
                    )
                ),
            )
            return

        if scenario.leaks:
            # No flag is touched, because no flag is involved. The condition
            # this stages is the process's own accumulation, which has been
            # going on for a while already - long enough that the window opens
            # quiet and the climb is visible in it the instant anybody looks.
            self._remember_where_the_flags_are_now()
            self._active = ActiveScenario(
                scenario=scenario,
                seeded_at=now,
                process_started_at=now - SETTLED_UPTIME,
                leak_started_at=now - timedelta(
                    minutes=get_scenario_settings().leak_backdate_minutes
                ),
            )
            return

        # The scenario's own flag, created here rather than at startup: a
        # scenario provisions the condition it stages, and one that is never
        # staged leaves the provider carrying nothing to explain. Idempotent, so
        # the ordinary case of a flag already there costs a read.
        self._flags_for(scenario).ensure_flag_exists(
            description_for(scenario.flag_role)
        )
        # Put the flag into its healthy state first, then into the breaking
        # one. The second call is the change that stages the incident, and the
        # first is what guarantees there *is* a second: a flag already sitting
        # in the breaking state would otherwise be switched to where it already
        # was, and an agent looking for what changed would find nothing.
        self._set_flag_for(scenario, scenario.healthy_flag_state)
        self._set_flag_for(scenario, not scenario.healthy_flag_state)
        # Staged the same way and in the same breath, so the provider records
        # both changes at the same moment. A decoy that arrived a minute later
        # would be distinguishable by its timestamp alone, and the incident
        # would stop being the ambiguous one it is meant to be.
        self._stage_the_decoy(scenario)
        # After staging, never before: the changes that stage an incident are
        # the incident, and recording them as moments would show the audience
        # the scenario answering itself.
        self._remember_where_the_flags_are_now()
        # Backdated so a diagnosable incident exists the instant this returns.
        # The alternative is an audience watching a flat graph for five minutes
        # before anything is worth alerting on.
        onset = now - _how_long_ago_this_one_began(scenario)
        # And the provider's log backdated with it, wherever a reader would
        # otherwise see the change arrive after the incident it caused.
        #
        # The onset above is moved back and the provider's own entry is not, so
        # the two disagree by exactly that much in every scenario staged by a
        # flag. Most can afford it: their incident is dated by a departure the
        # metrics show, a change a few minutes either side of it still reads as
        # the one that caused it, and no conclusion turns on the order.
        #
        # The drifting total cannot. It is dated a week back by the shop's own
        # check, so an unbackdated entry would sit a week from the onset and a
        # consumer looking around the onset would find an empty window.
        #
        # The incident dated by an absence has the sharpest version of this
        # problem - an entry recorded after the rows stop says the change cannot
        # be what stopped them - and it is not answered here, because that
        # scenario stages no flag at all. Its change is a deployment, and the
        # history a deployment lands in is the platform's.
        if scenario.drifts_the_monthly_total:
            self._backdate_the_flag_history(
                [self._flags_for(scenario).name], onset, now
            )
        self._active = ActiveScenario(
            scenario=scenario,
            seeded_at=now,
            timeline=FlagTimeline(turned_on_at=onset),
            decoy_timeline=(
                FlagTimeline(turned_on_at=onset)
                if scenario.decoy_flag_role is not None
                else None
            ),
            process_started_at=now - SETTLED_UPTIME,
            # A flag scenario like the four above it, and the only one that
            # needs anything beside the timeline. The other four break the shop
            # by what the flag routes traffic to; this one changes only what
            # those requests cost, and a cost is composed per request from the
            # path it took - which needs a cache for there to be a path to be
            # off. Staged working, and left working: nothing in this scenario
            # is wrong with the cache.
            #
            # The rollout itself is not stored. It is the flag's own timeline
            # said another way - out from the minute the flag went on, back the
            # minute it goes off - and a second record of one fact is a record
            # that comes to disagree with the first about when somebody
            # reverted.
            cache_endpoint=(
                the_working_cache_endpoint() if scenario.rollout_is_slow else None
            ),
        )

    @property
    def the_platform_will_not_act(self) -> bool:
        """Whether the deployment platform is refusing to carry an action.

        Per-scenario state rather than process state, unlike `syncs_itself` and
        `replicas` below it: those describe the arrangement the application runs
        under, and this describes an incident's world - true for as long as the
        scenario staging it is active, and over when that scenario is reset.

        It answers only for acting. What the platform *reports* is unaffected,
        which is the whole of how this stages a platform that cannot be
        mitigated through rather than one that cannot be seen.
        """
        active = self._active

        return active is not None and active.scenario.control_plane_is_down

    @property
    def syncs_itself(self) -> bool:
        """Whether the platform is reconciling this application on its own.

        On by default, which is how a GitOps deployment normally runs and is
        what makes suspending it a real step rather than a formality. Process
        state rather than per-scenario state: it describes the platform's
        arrangement with the application, not any incident, and a scenario
        seeding would no more reset it than it would reset the cluster.
        """
        return self._syncs_itself

    def set_automated_sync(self, enabled: bool) -> None:
        """Turns the platform's own reconciliation on or off.

        Both directions, because a mitigation that suspends it has to be able
        to put it back - and putting it back is the half of the undo that
        matters, since an application left un-reconciling is an application
        quietly not receiving anything anybody deploys to it.
        """
        self._syncs_itself = enabled

    @property
    def replicas(self) -> int:
        """How many replicas are serving, right now.

        Process state rather than per-scenario state, exactly as `syncs_itself`
        is: it describes the deployment's size, not any incident, and a scenario
        seeding would no more resize the deployment than it would reset the
        cluster. With nothing ever scaled it is the values file's count, because
        that is what the platform converged on before anybody interfered.
        """
        return self.capacity.replicas_during(utc_now())

    @property
    def capacity(self) -> Capacity:
        """How large the deployment has been, over time.

        A history and not a count, for exactly the reason the process's restarts
        are a history: a minute that has already been served was served by the
        capacity it had then. A single count that moved would flatten the
        incident retrospectively - every saturated minute would be regenerated at
        the size the deployment reached afterwards, and the stretch a mitigation
        wants to be judged against would disappear the moment it was performed.

        It is read on every generated minute, which is what makes a scale-out a
        mitigation anybody can perform and the telemetry has to answer for.
        """
        return Capacity(
            sized_for=the_deployed_replica_count(), scalings=self._scalings
        )

    @property
    def autoscaler(self) -> LiveAutoscaler | None:
        """The controller deciding the deployment's size, or `None` where none is.

        `None` for every scenario but the one that stages a flapping controller,
        and that is the whole of why it is a property rather than a field. A live
        autoscaler under every scenario would scale the saturated shop out on its
        own, and the scenario built to prove that adding capacity is the answer to
        saturation would answer itself before anybody was paged.

        Its floor is process state - the pins - and its ceiling, target and window
        come from the repository. So what a pin changes survives a mitigation being
        judged, and what the deployment is *declared* with survives everything: the
        estate's own bound is not a thing an incident moves.
        """
        active = self._active

        if active is None or not active.scenario.autoscaler_flaps:
            return None

        return LiveAutoscaler(
            declared=the_declared_autoscaler(), pins=self._pins
        )

    def pin_the_autoscaler_floor_to(self, floor: int) -> datetime:
        """Raises the floor the controller may fall to, and says when.

        Recorded as a moment rather than assigned, so the minutes already served
        keep the floor they were served under - see `autoscaler`. The moment is
        answered for the reason a resize's is: whoever asked is about to read the
        telemetry to see whether it worked, and what they read is dated against
        this.

        Both directions, because the floor is what an undo puts back. A withdrawal
        that could only raise it would leave the controller permanently unable to
        scale down, which is not the deployment anybody is meant to have returned
        to - and it is recorded the same way, as one more moment, so the stretch
        the shop spent held stays in the record too.

        It says nothing about whether the platform will leave it alone. A
        deployment still reconciling itself is one whose next sync re-applies the
        autoscaler the repository declares, floor included, and suspending that is
        the caller's business - `set_automated_sync` above - for the same reason it
        is the caller's business before a rollback or a scale-out.
        """
        at = utc_now()
        self._pins = (*self._pins, Pin(at=at, floor=floor))

        return at

    def scale_the_deployment_to(self, replicas: int) -> datetime:
        """Sets how many replicas are serving, and says when.

        Recorded as a moment rather than assigned, so the minutes already served
        keep the size they were served at - see `capacity`. The moment is
        answered for the reason a restart's is: whoever asked is about to look at
        the telemetry to see whether it worked, and what they will be reading is
        dated against this.

        Both directions, because the count is what an undo puts back. A
        withdrawal that could only add capacity would leave the shop permanently
        larger than the deployment it is meant to have returned to - and it is
        recorded the same way, as one more moment, so the stretch the shop spent
        large stays in the record too.

        It says nothing about whether the platform will leave it alone. A
        deployment still reconciling itself is one whose next sync sets this back
        to the values file's count, and suspending that is the caller's business
        - `set_automated_sync` above - for the same reason it is the caller's
        business before a rollback.
        """
        at = utc_now()
        self._scalings = (*self._scalings, Scaling(at=at, replicas=replicas))

        return at

    def roll_the_deployment_back(self) -> datetime:
        """Puts the shop on what the previous revision was running, and says
        when.

        What a platform rollback does, and all it does: what is running is made
        to agree with an earlier revision. Whichever of the two things a revision
        carries was the one that broke this, the rollback ends it - a
        configuration value the shop dials, or code every request executes - and
        a caller says only which application to return, exactly as the platform's
        own API does.

        Nothing in the repository changes. The values file still names the port
        and the branch still holds the slower code, which is why either incident
        is mitigated rather than resolved, and why re-enabling automated sync
        would bring it straight back.

        Ends a stretch rather than clearing it, for the reason a restart is
        recorded rather than erasing the climb: the minutes the shop spent
        unreachable or slow are what happened, and a window that lost them the
        moment somebody fixed it would take the incident out of the record
        exactly when a mitigation wants to be judged against it.

        Free on a shop with neither staged, which is what makes it safe for
        anybody to call: there is nothing to end, and the answer is simply when
        they asked.
        """
        at = utc_now()
        active = self._active

        if active is None:
            return at

        if active.scrape_outage is not None:
            # The fourth thing a rollback ends, and the only one where what
            # comes back is the evidence rather than the service. The shop was
            # well throughout; returning the deployment returns the port's old
            # name, the platform finds the target again, and the rows resume
            # from that minute. The minutes in between stay missing, because
            # nothing collected them and nothing keeps them.
            self._active = replace(
                active,
                scrape_outage=replace(active.scrape_outage, ended_at=at),
            )
        elif active.cache_outage is not None:
            self._active = replace(
                active,
                cache_outage=replace(active.cache_outage, ended_at=at),
                cache_endpoint=the_working_cache_endpoint(),
            )
        elif active.deploy_slowdown is not None:
            self._active = replace(
                active,
                deploy_slowdown=replace(active.deploy_slowdown, ended_at=at),
            )
        elif active.drifting_revision is not None:
            # The one a rollback ends least of. Purchases from here on
            # are written through the path that keeps the month, so the drift
            # stops growing; every total already written short stays short,
            # because a rollback changes what runs and not what it wrote.
            self._active = replace(
                active,
                drifting_revision=replace(active.drifting_revision, turned_off_at=at),
            )
        elif active.paused_rollout is not None:
            # The third thing a rollback ends, and the one it ends for a
            # different reason than the other two. There the revision carried
            # what was wrong and returning the deployment takes it away; here
            # neither revision carries anything wrong, and what returning the
            # deployment does is put every replica on one version - which is a
            # shop that works, whichever version it is.
            self._active = replace(
                active,
                paused_rollout=replace(active.paused_rollout, ended_at=at),
            )

        return at

    def withdraw_the_rollback(self) -> datetime:
        """Puts the shop back on the revision the rollback took it off, and says
        when.

        The other direction of the call above, and the platform's own: a
        rollback is addressed to a history entry, so returning an application to
        the entry it was on when somebody rolled it back is the same endpoint
        pointed the other way. That is what a withdrawal does - Argus undoes a
        mitigation by asking for the revision it found running.

        A fresh stretch rather than the old one reopened, for the reason a flag
        timeline starts a fresh one: this holds a single began-and-ended pair, so
        the minutes between the rollback and the withdrawal cannot be expressed
        as a gap, and claiming the fleet was split throughout would erase the
        evidence that the rollback worked - which is the one thing those minutes
        are read for.

        Three of the stretches a rollback ends are put back - the misconfigured
        cache, the slower revision and the paused rollout - so each of those
        incidents is back once its mitigation is withdrawn. The scrape outage and
        the drifting write path are not yet, and that is a limitation rather than
        a decision.

        Free on a shop with nothing a rollback ended, which is what makes it safe
        for anybody to call: there is nothing to put back, and the answer is
        simply when they asked.
        """
        at = utc_now()
        active = self._active

        if active is None:
            return at

        if active.cache_outage is not None and active.cache_outage.ended_at is not None:
            # The shop dials the port the deployed revision names again, and the
            # cache is as unreachable there as it was before the rollback.
            self._active = replace(
                active,
                cache_outage=CacheOutage(began_at=at),
                cache_endpoint=the_deployed_cache_endpoint(),
            )
        elif (active.deploy_slowdown is not None
              and active.deploy_slowdown.ended_at is not None):
            self._active = replace(active, deploy_slowdown=SlowDeployment(began_at=at))
        elif (active.paused_rollout is not None
              and active.paused_rollout.ended_at is not None):
            self._active = replace(active, paused_rollout=PausedRollout(began_at=at))

        return at

    def restart_the_shop(self) -> datetime:
        """Brings the serving process back, and says when.

        Two things happen, and both of them are the restart: what the shop had
        accumulated is gone, and the moment is recorded so that the telemetry
        reports a new process from here on. A restart that reclaimed the heap
        without moving the start time would be indistinguishable, from outside,
        from one that never happened - and that distinction is the only thing
        separating "the restart did not land" from "it landed and did not
        help".

        It works with nothing staged, and does the same thing. A platform
        restarts whatever is running, and refusing because this service has no
        scenario in mind would make the control lie about what it is.

        The climb then begins again, because a restart takes away what
        accumulated and not what accumulates it. That is the whole of why this
        mitigates a leak without resolving it.

        It ends no other service's condition, and that omission is load-bearing.
        A slow pricing service goes on being slow through as many restarts of the
        shop as anybody cares to perform, which is what makes restarting the shop
        a refutable mistake rather than an accidental fix.
        """
        at = utc_now()
        forget_every_visit()
        active = self._active

        if active is not None:
            self._active = replace(active, restarts=(*active.restarts, at))

        return at

    def restart_the_pricing_service(self) -> datetime:
        """Brings the pricing service's process back, and says when.

        Two things happen, and both of them are the restart: whatever had that
        service wedged is gone, so it answers promptly again, and its own start
        time moves so that the restart is confirmable from outside. A restart
        that fixed the latency without moving the start time would be
        indistinguishable from one that never happened.

        Only that service's start time moves. The shop's is untouched, which is
        what lets a reader - and a mitigation judging its own work - tell which
        of the two processes was actually restarted.

        The shop's own heap is untouched too. Nothing here reclaims anything of
        Io's, because nothing of Io's was wrong.

        Ends the stretch rather than clearing it, for the reason a rollback does:
        the minutes the shop spent waiting are what happened, and a window that
        lost them the moment somebody fixed it would take the incident out of the
        record exactly when a mitigation wants to be judged against it.

        Free on a shop with nothing staged, which is what makes it safe for
        anybody to call: there is nothing to end, and the answer is simply when
        they asked.
        """
        at = utc_now()
        active = self._active

        if active is None:
            return at

        self._active = replace(
            active,
            pricing_started_at=at,
            pricing_slowdown=(
                replace(active.pricing_slowdown, ended_at=at)
                if active.pricing_slowdown is not None
                else None
            )
        )

        return at

    def _stage_the_decoy(self, scenario: Scenario) -> None:
        """Moves the decoy flag, if the scenario has one, the way its role moves.

        Through the healthy state first, for the same reason the staged flag
        goes that way round: a flag already sitting where the scenario wants it
        records no change, and a decoy nothing recorded changing is not a
        suspect at all.
        """
        client = self._decoy_flags_for(scenario)

        if client is None:
            return

        client.ensure_flag_exists(description_for(scenario.decoy_flag_role))
        _set(client, _the_decoys_quiet_state(scenario))
        _set(client, not _the_decoys_quiet_state(scenario))

    def _put_the_flags_back_where_they_rest(self) -> None:
        """Both flags to their resting positions - the feature off, the fallback
        on - whatever this service believes it staged.

        Whatever it believes, because the belief is the part that goes missing.
        A service restarted mid-incident has no record of what it moved, and the
        flag left the wrong way round is as likely to be the fallback as the
        feature.

        A flag that cannot be moved is skipped rather than fatal. It may not be
        there at all: flags live in a provider anyone can reach, and a test
        suite clearing up between cases archives the ones it did not want -
        after which the provider refuses to toggle them. A flag that is absent
        is not a flag left in a breaking state, so there is nothing here to put
        right, and refusing to stage the next scenario over it would make this
        tidying step the reason nothing can be staged at all.

        A provider that is genuinely down still stops the caller: staging a
        scenario moves its own flag straight after this, and that call is not
        forgiving. What is skipped here is only the housekeeping.
        """
        for client, role in (
            (self._flags, FEATURE_FLAG),
            (self._fallback_flags, FALLBACK_FLAG),
        ):
            try:
                _set(client, quiet_state_for(role))
            except FlagProviderUnavailable:
                continue

    def _remember_where_the_flags_are_now(self) -> None:
        """Takes the baseline the next look is compared against.

        A flag that cannot be read is left out rather than guessed at: the first
        successful read then becomes the baseline, and the alternative - assuming
        a state - would report a change that never happened.
        """
        self._moments = []
        self._last_seen = {}

        for client in (self._flags, self._fallback_flags):
            try:
                self._last_seen[client.name] = client.is_enabled()
            except Exception:
                continue

    @property
    def moments(self) -> list[FlagMoment]:
        """Every flag change noticed since the scenario was staged, in order."""
        return list(self._moments)

    def the_integrity_check_found(self) -> Reconciliation:
        """What the shop's data-integrity job reports, run now.

        Worked out at the moment it is asked, exactly as the telemetry is, so that
        a flag somebody has just put back is already reflected in it: purchases
        recorded since the flip went through the path that keeps the total, so the
        count stops growing and not one stored figure is corrected. That
        difference - nothing new, everything old - is what recovery means for this
        mode, and there is no series it could be read from.

        A restart is nowhere in this, which is why a restart changes nothing about
        it. The fault is in what was written down, and the process that reads it
        back is a new one reading the same wrong totals.

        The check is not addressable from outside this service, and this method is
        the reason it can stay that way: the only caller is the monitoring stack
        deciding whether there is anything to page about - see
        `target_app.monitoring`.
        """
        return what_the_check_found(self._the_drifting_write_path(), utc_now())

    def the_cache_check_found(self) -> CacheReconciliation | None:
        """What the same job reports about the cache in front of those totals.

        `None` for every scenario but one, and that is what keeps every other
        shop from being asked a question it has no cache to answer. Where a
        scenario does stage a cache without staging a failover, there is nothing
        frozen in it and the comparison would find every entry in agreement -
        but asking at all would mean reading a store over the network on the way
        to every alert, for an answer known in advance.

        Worked out at the moment it is asked, as the totals check is, and for a
        sharper reason: this finding *grows*. An entry goes stale as soon as its
        shopper buys again, so the count a reader sees is the count when they
        asked - and an alert raised twice carries two different lists, both
        correct when they were made.

        The entries themselves are read from the staging rather than from the
        store. What the cache holds and what was written into it are the same
        thing for the whole of this scenario, because nothing rewrites an entry -
        and a check that read the store back would report Argus's own discard as
        a shrinking incident while the walk was still running.
        """
        active = self._active

        if active is None or not active.scenario.cache_failed_over:
            return None

        now = utc_now()

        return reconcile_cached_summaries(
            the_accounts_the_check_examines(None, now),
            the_figures_the_promoted_standby_holds(now)
        )

    @property
    def promoted_at(self) -> datetime | None:
        """When a lagging standby was put in front of shoppers, where one was."""
        return self._active.promoted_at if self._active else None

    def _the_drifting_write_path(self) -> FlagTimeline | None:
        """The stretch the cheaper write path has been live over, or `None`.

        `None` for every scenario but one, which is what keeps every other shop's
        totals in step with its purchases: the shop has one feature flag and
        several scenarios behind it, so what the flag is shipping is the
        scenario's to say and not the flag's - see
        `target_app.generator._serve_one_account_page`.

        Read off the flag's own timeline rather than stored beside it, for the
        reason the slow rollout's stretch is derived: the write path went live the
        minute the flag went on and stopped the minute it went off, and a second
        record of that would be one that comes to disagree with the first about
        when somebody reverted.
        """
        active = self._active

        if active is None or not active.scenario.drifts_the_monthly_total:
            return None

        if active.drifting_revision is not None:
            return active.drifting_revision

        return self.timeline_now()

    def the_minute_the_shop_went_quiet(self) -> datetime | None:
        """The first minute the shop published no metrics, or `None`.

        `None` for every scenario but one. A shop that is being collected from
        has no such minute, and nothing should be paged about silence that is
        not happening.

        Read off the outage rather than stored separately, so a deployment
        somebody has just rolled back is already reflected: the collecting
        stopped the minute the revision landed, and a second record of that
        would be one that comes to disagree with the first.

        Truncated to the minute, and it is the minute the revision landed in
        rather than the one before it - the same boundary `ScrapeOutage.covers`
        draws, and therefore the same minute the generator withholds. The two
        have to agree: this is the minute the alert states as its onset, and a
        consumer looking for the last row before it would otherwise find one
        that is missing, or one too many.
        """
        active = self._active

        if active is None or active.scrape_outage is None:
            return None

        return active.scrape_outage.began_at.replace(second=0, microsecond=0)

    def observe_the_flags(self) -> list[FlagMoment]:
        """Reads both flags and records any that have moved since the last look.

        On the read path, like every other reconciliation here, and for the same
        reason: nobody announces a flag change to this service, so the only
        moment it can find out is the moment somebody asks it something.

        Both flags every time, not only the staged one. An agent working an
        ambiguous incident will change a flag that turns out to be innocent and
        then change it back, and those two moments are the most interesting
        things on the page - they are the whole of what "it tried something,
        and it did not help" looks like from outside.

        A provider that cannot be read is not an error here. This feeds a
        display, and a page that fails to render because a flag could not be
        polled is worse than one that renders a moment late.
        """
        if self._active is None:
            return []

        for client in (self._flags, self._fallback_flags):
            try:
                enabled = client.is_enabled()
            except Exception:
                continue

            if self._last_seen.get(client.name) == enabled:
                continue

            self._last_seen[client.name] = enabled
            self._moments.append(
                FlagMoment(at=utc_now(), flag=client.name, enabled=enabled)
            )

        return self.moments

    def reset(self) -> None:
        """Clears the active scenario and any condition it left running.

        Both flags are put back to the state in which the shop is well, even if
        no scenario is active and even if this service does not believe it
        changed either. A flag left in its breaking state by an abandoned run is
        exactly what a reset is for, and refusing to clear it because the
        in-memory record disagrees would leave the next reader looking at an
        incident nobody started.

        Only the staged scenario's own flag is touched, and only to put it back
        where that scenario found it. Every flag change is evidence to whoever
        is investigating - the provider records it, and an agent identifies a
        culprit by asking what recently changed - so housekeeping on a flag no
        scenario staged would plant a second suspect beside the real one.

        With nothing staged there is no investigation to plant a suspect in
        front of, and both flags are put back where they rest - not just the
        feature flag. An abandoned run is exactly how a flag ends up moved with
        no scenario to remember it: the service restarts mid-incident, `_active`
        is gone, and the flag that is left switched the wrong way is as likely
        to be the fallback as the feature. Clearing only one of them left the
        shop broken and nothing claiming to be breaking it.

        Clearing a flag that is already clear is free: the provider records a
        toggle only where something actually moved, so a reset on a quiet shop
        adds nothing to the history that the next investigation will read.

        Then the recorded history of both flags goes, which is the half of a
        reset that putting the flags back does not do. The provider's log is
        what an investigation reads when it asks what recently changed, so a
        log still holding the last run's toggles - and the put-backs this
        method has just made - hands the next incident suspects that belong to
        a demo nobody is watching any more. Last, so that everything this reset
        itself recorded is inside what it clears.
        """
        active = self._active
        self._active = None
        self._moments = []
        self._last_seen = {}
        # Whatever the shop itself accumulated goes too, whichever scenario was
        # staged. It is the shop's own state rather than the scenario's, and a
        # reset that left it behind would hand the next run a heap it did not
        # start.
        forget_every_visit()
        # And whatever the shop left in its summary cache, for the same reason
        # and with a sharper consequence. A staged entry is a figure that
        # disagrees with the purchase ledger on purpose; one surviving a reset is
        # the same figure with nothing staged to explain it, and the next run's
        # integrity check reports it as an incident of its own - on a shop
        # nobody broke, in a scenario that never wrote to the cache at all.
        #
        # The whole keyspace rather than the active scenario's keys, because an
        # abandoned run is exactly how an entry outlives the record of who wrote
        # it. A cache that cannot be reached is left alone and that is not a
        # failure: the shop treats an unreachable cache as an ordinary day, and
        # one that never answered is holding nothing this reset could clear.
        forget_every_cached_summary()
        # The platform's arrangement with the application goes back too. A
        # rollback suspends automated sync and a withdrawal puts it back, but a
        # run abandoned between the two leaves it off - and the next scenario
        # would then be staged onto a deployment that silently reconciles
        # nothing, with its rollback accepted on the first try for reasons
        # belonging to the previous run.
        self._syncs_itself = True
        # And its size, for the same reason. A scale-out raises the count and a
        # withdrawal puts it back, but a run abandoned between the two leaves the
        # shop larger than its configuration - and the next scenario would then be
        # staged onto capacity the deployment never asked for, with a saturation
        # nobody could reproduce. The whole history goes, not just the latest
        # entry: what a reset produces is a deployment nobody has ever resized.
        self._scalings = ()
        # And its autoscaler's floor, for the same reason and with the same
        # consequence. A pin holds the count at the ceiling and a withdrawal puts
        # the floor back; a run abandoned between the two leaves the controller with
        # nowhere to scale down to, and the next flapping scenario would be staged
        # onto a deployment whose count cannot move - an incident that never starts.
        self._pins = ()

        if active is None:
            self._put_the_flags_back_where_they_rest()
            self._forget_what_the_flags_did()
            return

        if (
            active.scenario.leaks
            or active.scenario.upstream_fails
            or active.scenario.cache_is_misconfigured
        ):
            # Nothing to put back: none of these moved a flag - the process's
            # own accumulation, somebody else's service, and a value in a file
            # - and toggling one here would plant a change for the next
            # investigation to find. Clearing the active scenario is what ends
            # the condition, since nothing is staged for the generator to read.
            self._forget_what_the_flags_did()
            return

        self._set_flag_for(active.scenario, active.scenario.healthy_flag_state)
        decoy = self._decoy_flags_for(active.scenario)

        if decoy is not None:
            _set(decoy, _the_decoys_quiet_state(active.scenario))

        self._forget_what_the_flags_did()

    def _forget_what_the_flags_did(self) -> None:
        """Clears both flags' recorded history, whichever one was staged.

        Both, for the reason the flags themselves are both put back: a run
        abandoned by a restart left changes behind under a flag this service no
        longer remembers staging, and a history cleared by halves is a history
        the next investigation still finds something in.
        """
        self._forget_the_flag_history([self._flags.name, self._fallback_flags.name])

    def _set_flag_for(self, scenario: Scenario, enabled: bool) -> None:
        _set(self._flags_for(scenario), enabled)

    def phase(self) -> str:
        """Where the active scenario has got to.

        `running` and `recovering` are the phases during which something is
        happening that a click would disrupt; `complete` is when the window has
        stopped advancing and the next scenario can be staged. An authored
        scenario is `staged` for ever - it has no live condition, so there is
        no progress for it to be in the middle of.

        A leak reaches the same three phases by a different road: what ends its
        running phase is a restart rather than a flag going back, and what it
        settles into is a reclaimed heap climbing again rather than a rate that
        stayed down. Both are worth watching for the same few minutes.

        An upstream failure reaches only one of them. Nothing anybody may do
        here ends it, so it is `running` from the moment it is staged until
        somebody resets it - which is the phase telling the truth about a
        scenario whose condition belongs to another company.

        A misconfigured cache reaches all three, and what ends its running
        phase is the rollback: the moment the shop is put back on the address
        the cache actually listens on. Worth watching afterwards for the same
        reason a revert is - to see the median come back down and stay there.

        A slow deployment is the same three phases ended by the same act, and
        for the same reason it is worth watching after: what a rollback bought
        is visible only in the minutes that follow it.

        A slow dependency reaches all three as well, and what ends its running
        phase is a restart of the *other* service. Restarting this one leaves it
        running, which is the fixture declining to grade a wrong answer as a
        right one.

        A rollout stopped half-way reaches all three by the same act again, and
        it is the third scenario a rollback ends. What the settling minutes show
        here is not a curve coming down but a rate that went to nothing staying
        there - the fleet converges the instant the deployment is returned, and
        an incident whose mitigation is instantaneous is exactly the one a reader
        needs held open long enough to believe.
        """
        active = self._active

        if active is None:
            return IDLE

        window = self.generated_window()

        if window is None:
            return STAGED

        timeline, _ = window
        if active.scenario.leaks:
            ended_at = active.restarts[-1] if active.restarts else None
        elif active.scenario.cache_is_misconfigured:
            ended_at = (
                active.cache_outage.ended_at
                if active.cache_outage is not None
                else None
            )
        elif active.scenario.deploy_is_slow:
            ended_at = (
                active.deploy_slowdown.ended_at
                if active.deploy_slowdown is not None
                else None
            )
        elif active.scenario.rollout_is_paused:
            ended_at = (
                active.paused_rollout.ended_at
                if active.paused_rollout is not None
                else None
            )
        elif active.scenario.dependency_is_slow:
            ended_at = (
                active.pricing_slowdown.ended_at
                if active.pricing_slowdown is not None
                else None
            )
        elif active.scenario.surges:
            ended_at = self._relieved_at()
        elif active.scenario.autoscaler_flaps:
            ended_at = self._held_still_at()
        else:
            ended_at = timeline.turned_off_at if timeline is not None else None

        if ended_at is None:
            return RUNNING

        if utc_now() < _settled_at(ended_at):
            return RECOVERING

        return COMPLETE

    def _relieved_at(self) -> datetime | None:
        """When the deployment was last made larger than it is configured for, or
        `None` while it is not.

        What ends a surge's running phase, in the only terms a surge has: nothing
        stops the traffic, so the incident is over when the shop is big enough for
        it. Read from the count in force rather than from the fact that a resize
        happened, which is what makes it reconcile in both directions - a
        withdrawal that puts the count back returns this to `None`, and the phase
        to running, because the shop is saturated again.

        The *moment* is the resize that took it above the configured size, not the
        latest resize of any kind. A settling period is counted from this, and
        counting it from a resize that changed nothing about the saturation would
        freeze the window before the recovery it is meant to show.
        """
        sized_for = the_deployed_replica_count()

        if not self._scalings or self._scalings[-1].replicas <= sized_for:
            return None

        for scaling in reversed(self._scalings):
            if scaling.replicas <= sized_for:
                break

            relieved_at = scaling.at

        return relieved_at

    def _held_still_at(self) -> datetime | None:
        """When the autoscaler was last left with no room to scale down, or `None`
        while it still has some.

        What ends a flapping scenario's running phase, in the only terms it has:
        nothing stops the traffic and nothing removes the controller, so the
        incident is over when the count stops moving. Read from the floor in force
        against the ceiling rather than from the fact that a pin happened, which is
        what makes it reconcile in both directions - a withdrawal that puts the
        floor back returns this to `None`, and the phase to running, because the
        shop is flapping again.

        The *moment* is the pin that closed the gap, not the latest pin of any
        kind. A settling period is counted from this, and counting it from a pin
        that left the controller room to shrink would freeze the window before the
        recovery it is meant to show.
        """
        autoscaler = self.autoscaler

        if autoscaler is None or not autoscaler.pins:
            return None

        if autoscaler.pins[-1].floor < autoscaler.ceiling:
            return None

        held_still_at = None

        for pin in reversed(autoscaler.pins):
            if pin.floor < autoscaler.ceiling:
                break

            held_still_at = pin.at

        return held_still_at

    def generated_window(self) -> tuple[FlagTimeline | None, datetime] | None:
        """The active generated scenario's timeline, and the instant its
        telemetry runs up to - or `None` if no generated scenario is active.

        The second half is what ends a scenario. While the flag is on, and for
        a settling period after it goes off, that instant is simply now. Past
        the settling period it stops moving, so the window freezes with the
        recovery in it: the incident, the drop, and enough clean minutes after
        the drop to show it held.

        Freezing rather than clearing, because the point of a demo is to be
        looked at after it finishes. Clearing is what `reset` is for.

        A leaking scenario has no flag and so no timeline, and answers `None`
        in its place. What ends it is the last restart, settled the same way -
        the point of watching a few minutes past a restart is to see the heap
        come back down and start climbing again, which is the evidence that the
        incident was mitigated and not fixed.
        """
        active = self._active

        if active is not None and active.scenario.upstream_fails:
            # It runs up to now for as long as it is staged, and there is no
            # settling instant to freeze it at: nothing Argus may do ends this
            # one, so there is never a recovery to hold the window open around.
            # A reset is what stops it, which is a person deciding to stop it.
            return None, utc_now()

        if active is not None and active.scenario.stops_publishing_telemetry:
            # No flag here either, and what ends it is the rollback the two
            # below are ended by - settled the same way, and for a reason
            # neither of them has. There is no level to watch come back down:
            # what returns is the rows themselves, so the settling minutes are
            # the only evidence the mitigation worked at all. A window frozen at
            # the rollback would end on the last blind minute and show a shop
            # still unseen.
            if active.scrape_outage is None or active.scrape_outage.ended_at is None:
                return None, utc_now()

            return None, min(utc_now(), _settled_at(active.scrape_outage.ended_at))

        if active is not None and active.scenario.cache_is_misconfigured:
            # No flag here either. What ends this one is the rollback, settled
            # the same way a revert is: the point of watching a few minutes
            # past it is to see the median come back down and stay there,
            # which is the only evidence the mitigation worked - and the tail
            # will not confirm it, having never moved.
            if active.cache_outage is None or active.cache_outage.ended_at is None:
                return None, utc_now()

            return None, min(utc_now(), _settled_at(active.cache_outage.ended_at))

        if active is not None and active.scenario.deploy_is_slow:
            # No flag here either, and what ends it is the same rollback the
            # misconfiguration is ended by - settled the same way, and for a
            # better reason than either: every quantile moved, so every quantile
            # has to be seen coming back down before a mitigation can be said to
            # have worked.
            if active.deploy_slowdown is None or active.deploy_slowdown.ended_at is None:
                return None, utc_now()

            return None, min(utc_now(), _settled_at(active.deploy_slowdown.ended_at))

        if active is not None and active.drifting_revision is not None:
            # No flag, and nothing in the window to settle. Every minute is flat
            # whether the revision is running or not, so there is no recovery to
            # hold the window open around - it runs up to now until the rollback
            # and stops a settling period after it, only so that the scenario is
            # reported complete the way every rollback scenario is.
            if active.drifting_revision.turned_off_at is None:
                return None, utc_now()

            return None, min(
                utc_now(), _settled_at(active.drifting_revision.turned_off_at)
            )

        if active is not None and active.scenario.rollout_is_paused:
            # No flag, and what ends it is the same rollback the two scenarios
            # above are ended by - settled the same way, and for a reason of its
            # own. Nothing here comes *back down*: the error rate steps to zero
            # the moment the fleet converges, so what the settling minutes are
            # for is showing that it stayed there, which is the whole of what a
            # reader is owed by an incident whose mitigation is instantaneous.
            if active.paused_rollout is None or active.paused_rollout.ended_at is None:
                return None, utc_now()

            return None, min(utc_now(), _settled_at(active.paused_rollout.ended_at))

        if active is not None and active.scenario.dependency_is_slow:
            # No flag, and what ends it is a restart - but not the restart the
            # leak is ended by, and not this application's. The stretch closes
            # when the pricing service comes back, which is why the condition is
            # read here rather than `restarts`: a shop restarted a dozen times
            # leaves this exactly where it was.
            if (
                active.pricing_slowdown is None
                or active.pricing_slowdown.ended_at is None
            ):
                return None, utc_now()

            return None, min(utc_now(), _settled_at(active.pricing_slowdown.ended_at))

        if active is not None and active.scenario.leaks:
            if not active.restarts:
                return None, utc_now()

            return None, min(utc_now(), _settled_at(active.restarts[-1]))

        if active is not None and active.scenario.surges:
            # No flag, and nothing that ends the condition at all - the traffic
            # goes on arriving. What ends the *incident* is the shop being large
            # enough for it, so the settling period is counted from the resize that
            # made it so, and for the reason the deployment scenario's is counted
            # from its rollback: every quantile moved, so every quantile has to be
            # seen coming back down before the mitigation can be said to have
            # worked.
            relieved_at = self._relieved_at()

            if relieved_at is None:
                return None, utc_now()

            return None, min(utc_now(), _settled_at(relieved_at))

        if active is not None and active.scenario.autoscaler_flaps:
            # No flag, and nothing that ends the condition either: the traffic goes
            # on arriving and the controller goes on deciding. What ends the
            # *incident* is the count stopping, so the settling period is counted
            # from the pin that stopped it - and counted at all for the reason the
            # surge's is, that every quantile moved and every quantile has to be
            # seen coming back down before the mitigation can be said to have
            # worked.
            held_still_at = self._held_still_at()

            if held_still_at is None:
                return None, utc_now()

            return None, min(utc_now(), _settled_at(held_still_at))

        timeline = self.timeline_now()

        if timeline is None:
            return None

        if timeline.turned_off_at is None:
            return timeline, utc_now()

        return timeline, min(utc_now(), _settled_at(timeline.turned_off_at))

    def timeline_now(self) -> FlagTimeline | None:
        """The active generated scenario's flag timeline, reconciled against
        the provider - or `None` if no generated scenario is active.

        Called on the read path, which is what makes the reconciliation
        timely: the reader who is about to be shown recovery is the one whose
        request records that recovery began.
        """
        active = self._active

        if active is None or active.timeline is None:
            return None

        # A scenario whose fault is not the flag's doing never ends, however the
        # flag moves. Reverting it is then a real action against a real cause
        # that was not the cause - which an agent should discover from the
        # metrics rather than be told.
        if not active.scenario.recovers_when_flag_reverts:
            return active.timeline

        broken_now = self._is_in_the_breaking_state(active.scenario)

        if not _has_fallen_behind(active.timeline, broken_now):
            return active.timeline

        caught_up = _caught_up_with_the_flag(active.timeline, broken_now)
        self._active = replace(active, timeline=caught_up)

        return caught_up

    def decoy_timeline_now(self) -> FlagTimeline | None:
        """The decoy flag's history, reconciled against the provider.

        Reconciled on the read path exactly as the staged flag is, and for the
        opposite reason: nothing about the incident changes when the decoy
        moves, so nothing else would ever notice that it had. What it changes is
        the log line, and a fixture whose logs still report a flag as on after
        an agent switched it off would be lying in the one channel that agent
        reads to find out what it just did.
        """
        active = self._active

        if active is None or active.decoy_timeline is None:
            return None

        client = self._decoy_flags_for(active.scenario)

        if client is None:
            return active.decoy_timeline

        moved_now = client.is_enabled() is not _the_decoys_quiet_state(
            active.scenario
        )

        if not _has_fallen_behind(active.decoy_timeline, moved_now):
            return active.decoy_timeline

        caught_up = _caught_up_with_the_flag(active.decoy_timeline, moved_now)
        self._active = replace(active, decoy_timeline=caught_up)

        return caught_up

    def _is_in_the_breaking_state(self, scenario: Scenario) -> bool:
        """Whether the flag still sits where it broke the shop.

        Asked as "is it still broken" rather than "is it on", because on is the
        breaking state for one scenario and the healthy state for another.
        """
        return self._flags_for(scenario).is_enabled() is not scenario.healthy_flag_state
