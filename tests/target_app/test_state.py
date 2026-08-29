from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

from target_app.flags import FlagClient
from target_app.generator import utc_now
from target_app.scenarios import (
    BAD_DEPLOYMENT,
    FALLBACK_DISABLED,
    FEATURE_FLAG_TOGGLE,
    FLAG_TOGGLE_RED_HERRING,
    SCENARIOS,
)
from target_app.settings import get_scenario_settings
from target_app.state import (
    COMPLETE,
    IDLE,
    RECOVERING,
    RUNNING,
    ScenarioState,
)

"""Staging a scenario, and keeping it honest against a flag anyone can change.

The reconciliation is the part worth pinning. This service is never told that
the flag went off - it finds out by asking, on the next read - and the whole
design rests on that, because the party who turns the flag off is usually
Argus and sometimes a human in a console.
"""


def a_flag_client_reporting(enabled: bool) -> Mock:
    flags = Mock(spec=FlagClient)
    flags.is_enabled.return_value = enabled
    return flags


def a_scenario_state(flags: Mock, fallback_flags: Mock | None = None) -> ScenarioState:
    """A state object whose second flag nobody is looking at.

    Most cases here stage the feature flag, and the fallback flag only has to
    exist for them - so it is defaulted rather than restated, and named
    explicitly by the cases that are actually about it.
    """
    return ScenarioState(flags, fallback_flags or a_flag_client_reporting(True))


def test_seeding_a_generated_scenario_turns_the_flag_on() -> None:
    flags = a_flag_client_reporting(True)

    a_scenario_state(flags).seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    flags.enable.assert_called_once()


def test_seeding_an_authored_scenario_leaves_the_flag_alone() -> None:
    # `bad-deployment` stages a deploy, not a flag. Touching the flag here
    # would put a second, unrelated incident into the same window.
    flags = a_flag_client_reporting(False)

    a_scenario_state(flags).seed(SCENARIOS[BAD_DEPLOYMENT])

    flags.enable.assert_not_called()


def test_seeding_backdates_the_onset_so_an_incident_already_exists() -> None:
    # Otherwise a freshly seeded scenario is a flat graph, and there is nothing
    # to alert on until several minutes have passed.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)

    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    backdate = get_scenario_settings().onset_backdate_minutes
    age_seconds = (utc_now() - state.active.timeline.turned_on_at).total_seconds()
    assert age_seconds >= backdate * 60


def test_a_generated_scenario_has_no_end_while_its_flag_is_on() -> None:
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    assert state.timeline_now().turned_off_at is None


def test_a_flag_turned_off_by_anyone_ends_the_incident() -> None:
    # Nobody tells this service. It asks, on the read that was about to show
    # the incident, and the answer is what ends it - which is what lets a
    # mitigation attempt be graded rather than believed.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    flags.is_enabled.return_value = False

    assert state.timeline_now().turned_off_at is not None


def test_the_incident_stays_ended_once_it_has_ended() -> None:
    # The end time is stamped once. Re-stamping it on every later read would
    # walk the recovery forward in time and erase the minutes that recovered.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    flags.is_enabled.return_value = False

    first = state.timeline_now().turned_off_at
    second = state.timeline_now().turned_off_at

    assert first == second


def test_an_ended_incident_stays_active_as_a_scenario() -> None:
    # Recovery is not the same as un-staging. The scenario is still the one
    # running, and its recovered minutes are exactly what a verifier reads.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    flags.is_enabled.return_value = False

    state.timeline_now()

    assert state.active_scenario_id == FEATURE_FLAG_TOGGLE


def test_resetting_clears_the_scenario_and_the_flag() -> None:
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    # Seeding switches the flag off before switching it on, so that staging is
    # a change even when the flag was already where the scenario wants it.
    # Counted from here so this stays about what `reset` does.
    disables_before_the_reset = flags.disable.call_count

    state.reset()

    assert state.active_scenario_id is None
    assert flags.disable.call_count == disables_before_the_reset + 1


def test_resetting_clears_a_flag_left_on_by_someone_else() -> None:
    # A flag left on by an abandoned run is exactly the state a reset is for.
    # Refusing to clear it because this process has no memory of staging it
    # would leave the next reader looking at an incident nobody started.
    flags = a_flag_client_reporting(True)

    a_scenario_state(flags).reset()

    flags.disable.assert_called_once()


def test_a_live_incident_reports_itself_as_running() -> None:
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    assert state.phase() == RUNNING


def test_a_just_reverted_incident_reports_itself_as_recovering() -> None:
    # Not yet finished. The drop has happened, but a drop with nothing after it
    # shows the number went down, not that it stayed down.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    flags.is_enabled.return_value = False

    assert state.phase() == RECOVERING


def test_nothing_staged_reports_itself_as_idle() -> None:
    assert a_scenario_state(a_flag_client_reporting(False)).phase() == IDLE


def test_a_live_incident_generates_up_to_now() -> None:
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    _, up_to = state.generated_window()

    a_generous_allowance_seconds = 5
    assert (utc_now() - up_to).total_seconds() < a_generous_allowance_seconds


def test_a_recovering_incident_still_generates_up_to_now() -> None:
    # The clean minutes after the revert are the proof that mitigation worked,
    # so they have to keep arriving until there are enough of them.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    flags.is_enabled.return_value = False
    state.timeline_now()

    _, up_to = state.generated_window()

    a_generous_allowance_seconds = 5
    assert (utc_now() - up_to).total_seconds() < a_generous_allowance_seconds


def test_a_settled_incident_stops_advancing() -> None:
    # What ends a scenario. Past the settling period the window freezes with the
    # recovery in it, so the next scenario can be staged against a page that is
    # no longer moving - and the finished one is still there to be looked at.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    flags.is_enabled.return_value = False
    state.timeline_now()

    settle = timedelta(minutes=get_scenario_settings().settle_minutes)
    long_ago = utc_now() - settle - timedelta(minutes=1)
    state._active = replace(
        state.active, timeline=replace(state.active.timeline, turned_off_at=long_ago)
    )

    assert state.phase() == COMPLETE
    _, up_to = state.generated_window()
    assert up_to == long_ago + settle


def test_an_authored_scenario_has_no_timeline_to_reconcile() -> None:
    flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[BAD_DEPLOYMENT])

    assert state.timeline_now() is None


def test_seeding_the_fallback_scenario_switches_its_own_flag_off() -> None:
    # The other direction: this incident begins when a flag goes off, so
    # staging it means switching one off rather than on - and the feature flag,
    # which has nothing to do with this scenario, is left alone.
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags, fallback_flags)

    state.seed(SCENARIOS[FALLBACK_DISABLED])

    fallback_flags.disable.assert_called_once()
    flags.disable.assert_not_called()
    flags.enable.assert_not_called()


def test_the_fallback_incident_ends_when_its_flag_goes_back_on() -> None:
    # Recovery is the flag returning to the state the shop is well in, which
    # for this flag is on. A service that only understood "off means better"
    # would report this incident as still running after it was fixed.
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[FALLBACK_DISABLED])

    fallback_flags.is_enabled.return_value = True

    assert state.timeline_now().turned_off_at is not None


def test_the_fallback_incident_is_still_running_while_its_flag_is_off() -> None:
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)

    state.seed(SCENARIOS[FALLBACK_DISABLED])

    assert state.timeline_now().turned_off_at is None


def test_a_coincidental_flag_toggle_does_not_end_when_the_flag_is_reverted() -> None:
    # The flag really was switched on and really is not the cause. Reverting it
    # is a reasonable thing to have tried and changes nothing, which is what
    # makes the attempt refutable rather than confirmable.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FLAG_TOGGLE_RED_HERRING])

    flags.is_enabled.return_value = False

    assert state.timeline_now().turned_off_at is None
    assert state.phase() == RUNNING


def test_resetting_puts_the_staged_scenario_s_own_flag_back() -> None:
    # Healthy is not the same state for both flags: the feature flag is well
    # off and the fallback flag is well on. A reset that switched everything
    # off would leave the shop sitting in the fallback scenario's incident with
    # nothing staged.
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[FALLBACK_DISABLED])

    state.reset()

    assert fallback_flags.enable.call_count == 2  # staging it, then putting it back


def test_resetting_leaves_alone_a_flag_no_scenario_staged() -> None:
    # Every flag change is evidence to whoever investigates the next incident.
    # Tidying a flag this scenario never touched would plant a second suspect
    # beside the real one, and an agent that cannot tell which flag an incident
    # is about escalates instead of acting.
    flags = a_flag_client_reporting(True)
    fallback_flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    state.reset()

    fallback_flags.enable.assert_not_called()
    fallback_flags.disable.assert_not_called()
