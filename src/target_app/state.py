from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from target_app.flags import FlagClient
from target_app.generator import FlagTimeline
from target_app.scenarios import Scenario, utc_now
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

    def __init__(self, flags: FlagClient) -> None:
        self._flags = flags
        self._active: ActiveScenario | None = None

    @property
    def active(self) -> ActiveScenario | None:
        return self._active

    @property
    def active_scenario_id(self) -> str | None:
        return self._active.scenario.id if self._active else None

    def seed(self, scenario: Scenario) -> None:
        """Stages a scenario, establishing whatever live condition it needs."""
        now = utc_now()

        if not scenario.is_generated:
            self._active = ActiveScenario(scenario=scenario, seeded_at=now)
            return

        self._flags.enable()
        self._active = ActiveScenario(
            scenario=scenario,
            seeded_at=now,
            # Backdated so a diagnosable incident exists the instant this
            # returns. The alternative is an audience watching a flat graph for
            # five minutes before anything is worth alerting on.
            timeline=FlagTimeline(
                turned_on_at=now
                - timedelta(minutes=get_scenario_settings().onset_backdate_minutes)
            ),
        )

    def reset(self) -> None:
        """Clears the active scenario and any condition it left running.

        The flag goes off even if no scenario is active, and even if this
        service does not believe it turned it on. A flag left on by an
        abandoned run is exactly the state a reset is for, and refusing to
        clear it because the in-memory record disagrees would leave the next
        reader looking at an incident nobody started.
        """
        self._active = None
        self._flags.disable()

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

        if self._flags.is_enabled():
            return active.timeline

        ended = replace(active.timeline, turned_off_at=utc_now())
        self._active = replace(active, timeline=ended)
        return ended
