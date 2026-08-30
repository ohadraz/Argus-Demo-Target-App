from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from target_app.flags import FlagClient, FlagProviderUnavailable
from target_app.generator import FlagTimeline
from target_app.history import forget_the_changes_to
from target_app.scenarios import (
    FALLBACK_FLAG,
    FEATURE_FLAG,
    Scenario,
    description_for,
    quiet_state_for,
    utc_now,
)
from target_app.settings import get_scenario_settings

"""What is currently staged, and keeping it honest against the live flag.

In-memory only: a restart clears it. The scenario definitions are code, not
state, so they survive restarts unaffected.
"""

# The phases a scenario passes through, as anything watching them sees it.
# `RUNNING` and `RECOVERING` are the ones during which something is happening
# that an interruption would spoil; `COMPLETE` says the window has stopped
# advancing and the next scenario can be staged.
IDLE = "idle"
STAGED = "staged"
RUNNING = "running"
RECOVERING = "recovering"
COMPLETE = "complete"


def _settled_at(turned_off_at: datetime) -> datetime:
    return turned_off_at + timedelta(minutes=get_scenario_settings().settle_minutes)


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
    timeline: FlagTimeline | None = None
    # The decoy's own history, for a scenario that stages one. Separate from
    # `timeline` because the two diverge the moment somebody reverts the decoy:
    # that revert is real and belongs in the logs, and it ends nothing.
    decoy_timeline: FlagTimeline | None = None


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
    ) -> None:
        self._flags = flags
        self._fallback_flags = fallback_flags
        self._forget_the_flag_history = forget_the_flag_history
        self._active: ActiveScenario | None = None
        self._moments: list[FlagMoment] = []
        self._last_seen: dict[str, bool] = {}

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
            self._active = ActiveScenario(scenario=scenario, seeded_at=now)
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
        onset = now - timedelta(minutes=get_scenario_settings().onset_backdate_minutes)
        self._active = ActiveScenario(
            scenario=scenario,
            seeded_at=now,
            timeline=FlagTimeline(turned_on_at=onset),
            decoy_timeline=(
                FlagTimeline(turned_on_at=onset)
                if scenario.decoy_flag_role is not None
                else None
            ),
        )

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

        if active is None:
            self._put_the_flags_back_where_they_rest()
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
        """
        active = self._active

        if active is None:
            return IDLE

        window = self.generated_window()

        if window is None:
            return STAGED

        timeline, _ = window

        if timeline.turned_off_at is None:
            return RUNNING

        if utc_now() < _settled_at(timeline.turned_off_at):
            return RECOVERING

        return COMPLETE

    def generated_window(self) -> tuple[FlagTimeline, datetime] | None:
        """The active generated scenario's timeline, and the instant its
        telemetry runs up to - or `None` if no generated scenario is active.

        The second half is what ends a scenario. While the flag is on, and for
        a settling period after it goes off, that instant is simply now. Past
        the settling period it stops moving, so the window freezes with the
        recovery in it: the incident, the drop, and enough clean minutes after
        the drop to show it held.

        Freezing rather than clearing, because the point of a demo is to be
        looked at after it finishes. Clearing is what `reset` is for.
        """
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

        if active.timeline.turned_off_at is not None:
            return active.timeline

        # A scenario whose fault is not the flag's doing never ends, however the
        # flag moves. Reverting it is then a real action against a real cause
        # that was not the cause - which an agent should discover from the
        # metrics rather than be told.
        if not active.scenario.recovers_when_flag_reverts:
            return active.timeline

        if self._is_in_the_breaking_state(active.scenario):
            return active.timeline

        ended = replace(active.timeline, turned_off_at=utc_now())
        self._active = replace(active, timeline=ended)
        return ended

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

        if active.decoy_timeline.turned_off_at is not None:
            return active.decoy_timeline

        client = self._decoy_flags_for(active.scenario)

        if client is None or client.is_enabled() is not _the_decoys_quiet_state(
            active.scenario
        ):
            return active.decoy_timeline

        reverted = replace(active.decoy_timeline, turned_off_at=utc_now())
        self._active = replace(active, decoy_timeline=reverted)
        return reverted

    def _is_in_the_breaking_state(self, scenario: Scenario) -> bool:
        """Whether the flag still sits where it broke the shop.

        Asked as "is it still broken" rather than "is it on", because on is the
        breaking state for one scenario and the healthy state for another.
        """
        return self._flags_for(scenario).is_enabled() is not scenario.healthy_flag_state
