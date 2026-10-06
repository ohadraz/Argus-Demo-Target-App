from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from io_shop.visits import (
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
)
from target_app.flags import FlagClient, FlagProviderUnavailable
from target_app.generator import TIMESTAMP_FORMAT, generate, utc_now
from target_app.scenarios import (
    BAD_DEPLOYMENT,
    CACHE_MISCONFIGURED,
    CATEGORISER_MODEL_UPGRADED,
    COMPETING_FLAG_CHANGES,
    CPU_SATURATION,
    FALLBACK_DISABLED,
    FEATURE_FLAG_TOGGLE,
    FLAG_TOGGLE_RED_HERRING,
    HALF_FINISHED_ROLLOUT,
    MONITORING_BLIND_SPOT,
    MONITORING_CONFIGURATION_DRIFT,
    MONTHLY_TOTALS_FALLING_BEHIND,
    PRICING_SERVICE_DEGRADED,
    RESOURCE_LEAK,
    SCENARIOS,
    SILENT_DATA_CORRUPTION,
    SLOW_CANARY_ROLLOUT,
    UPSTREAM_DEPENDENCY_FAILURE,
    Scenario,
)
from target_app.settings import get_scenario_settings, the_working_cache_endpoint
from target_app.state import (
    CLEAN_MINUTES_SHOWN_AFTER_RECOVERY,
    COMPLETE,
    IDLE,
    RECOVERING,
    RUNNING,
    ScenarioState,
)


def present[T](value: T | None) -> T:
    """`value`, which the case staged and so cannot be `None`.

    Said once rather than as an `assert` before every read, so a case reads as
    what it checks rather than as the narrowing it took to get there.
    """
    assert value is not None, "Expected a value the case staged, and found None."
    return value


@pytest.fixture(autouse=True)
def a_shop_that_has_just_started() -> None:
    """Every case begins with the shop holding nothing.

    What the account page retains is module state, so without this each case
    would inherit whatever the last one left behind - which is the fault the
    leak scenario is about, and a poor thing to also have in the tests about it.
    """
    forget_every_visit()


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


def a_scenario_state(
    flags: Mock,
    fallback_flags: Mock | None = None,
    forget_the_flag_history: Mock | None = None,
    backdate_the_flag_history: Mock | None = None,
) -> ScenarioState:
    """A state object whose second flag nobody is looking at.

    Most cases here stage the feature flag, and the fallback flag only has to
    exist for them - so it is defaulted rather than restated, and named
    explicitly by the cases that are actually about it.

    Clearing the flag history is stubbed for the same reason, and for one more:
    the real one reaches the provider's own database, so a reset here would go
    looking for a database no unit test has. Backdating it is stubbed for exactly
    that second reason - it reaches the same database - and defaulted because only
    the scenario whose onset is days old ever asks for it.
    """
    return ScenarioState(
        flags,
        fallback_flags or a_flag_client_reporting(True),
        forget_the_flag_history or Mock(),
        backdate_the_flag_history or Mock(),
    )


def the_first_whole_minute_after(moment: datetime) -> datetime:
    """The first minute that is entirely after `moment`.

    The minute a revert lands in is part broken and part clean, so it is the one
    after it that carries the recovery on its own.
    """
    return moment.replace(second=0, microsecond=0) + timedelta(minutes=1)


def where_it_was_left(client: Mock) -> bool:
    """The position the last call put this flag in.

    Asserted on rather than counting calls, because a real provider ignores a
    toggle that changes nothing and a `Mock` cannot. A count measures how many
    times this service asked, which is not a fact about the shop; where the flag
    ended up is.
    """
    moves = [call for call in client.method_calls if call[0] in ("enable", "disable")]

    assert moves, "nothing ever moved this flag"

    return moves[-1][0] == "enable"


def test_seeding_a_generated_scenario_turns_the_flag_on() -> None:
    flags = a_flag_client_reporting(True)

    a_scenario_state(flags).seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    flags.enable.assert_called_once()


def test_seeding_an_authored_scenario_stages_no_flag_incident() -> None:
    # `bad-deployment` stages a deploy, not a flag. Switching one on here would
    # put a second, unrelated incident into the same window.
    flags = a_flag_client_reporting(False)

    a_scenario_state(flags).seed(SCENARIOS[BAD_DEPLOYMENT])

    flags.enable.assert_not_called()


def test_seeding_starts_from_a_shop_nobody_has_left_broken() -> None:
    # The guard against staging one scenario on top of the last one's residue,
    # and it lives here rather than in the console because the button is not the
    # only way in.
    #
    # The residue is usually not the previous scenario's staged flag, which is
    # what makes it easy to miss: the ambiguous scenario can finish with its
    # decoy still switched on, having been reverted and put back by an agent
    # that found it innocent. The deployment scenario staged next touches no
    # flags at all, so nothing would correct it, and the investigation would
    # open on a shop carrying a change from an incident that was already over.
    left_switched_on_by_an_earlier_run = a_flag_client_reporting(True)

    a_scenario_state(left_switched_on_by_an_earlier_run).seed(SCENARIOS[BAD_DEPLOYMENT])

    assert where_it_was_left(left_switched_on_by_an_earlier_run) is False


def test_seeding_backdates_the_onset_so_an_incident_already_exists() -> None:
    # Otherwise a freshly seeded scenario is a flat graph, and there is nothing
    # to alert on until several minutes have passed.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)

    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    backdate = get_scenario_settings().onset_backdate_minutes
    age_seconds = (utc_now() - present(present(state.active).timeline).turned_on_at).total_seconds()
    assert age_seconds >= backdate * 60


def test_a_generated_scenario_has_no_end_while_its_flag_is_on() -> None:
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    assert present(state.timeline_now()).turned_off_at is None


def test_a_flag_turned_off_by_anyone_ends_the_incident() -> None:
    # Nobody tells this service. It asks, on the read that was about to show
    # the incident, and the answer is what ends it - which is what lets a
    # mitigation attempt be graded rather than believed.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    flags.is_enabled.return_value = False

    assert present(state.timeline_now()).turned_off_at is not None


def test_the_incident_stays_ended_once_it_has_ended() -> None:
    # The end time is stamped once. Re-stamping it on every later read would
    # walk the recovery forward in time and erase the minutes that recovered.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    flags.is_enabled.return_value = False

    first = present(state.timeline_now()).turned_off_at
    second = present(state.timeline_now()).turned_off_at

    assert first == second


def test_a_flag_switched_back_on_starts_the_incident_again() -> None:
    # What every failed mitigation leaves behind. Argus undoes an action that did
    # not help, so a flag it switched off is switched on again a few minutes
    # later - and the feature is then live for a second time.
    #
    # Reconciled in one direction only, which is how this was written, the
    # timeline froze at the first revert: the shop went on reporting healthy
    # minutes with the feature live, and every later verdict rested on telemetry
    # that no longer followed the flag. A fixture that lies about the state an
    # agent is about to act on is worse than one that stages nothing.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    flags.is_enabled.return_value = False
    ended = present(state.timeline_now())

    flags.is_enabled.return_value = True
    live_again = present(state.timeline_now())

    assert ended.turned_off_at is not None
    assert live_again.turned_off_at is None
    assert live_again.turned_on_at >= ended.turned_off_at


def test_a_flag_switched_back_on_keeps_the_stretch_before_the_revert() -> None:
    # The minutes the incident first ran are what happened, and the re-enable does
    # not unhappen them. Dropped, they read as quiet - and an agent investigating
    # again after its revert was refuted found a window with no departure in it
    # and called the alarm false.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    first_stretch = present(state.timeline_now())

    flags.is_enabled.return_value = False
    ended = present(state.timeline_now())

    flags.is_enabled.return_value = True
    live_again = present(state.timeline_now())

    assert live_again.earlier == (ended,)
    assert live_again.first_turned_on_at == first_stretch.turned_on_at


def test_an_incident_started_again_does_not_freeze_the_window() -> None:
    # The consequence the reconciliation exists for. A window frozen at the first
    # revert stops advancing, so the minutes an agent reads to judge its second
    # action are the same minutes it read to judge its first.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    flags.is_enabled.return_value = False
    state.timeline_now()

    flags.is_enabled.return_value = True

    _, up_to = present(state.generated_window())
    a_generous_allowance_seconds = 5

    assert (utc_now() - up_to).total_seconds() < a_generous_allowance_seconds


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


def test_resetting_clears_what_the_provider_recorded_about_both_flags() -> None:
    # The half of a reset that putting the flags back does not do. The
    # provider's log is what an investigation reads when it asks what recently
    # changed, so toggles left in it from the run just finished - and the
    # put-backs the reset itself just made - become suspects in the next
    # incident that nobody staged.
    forget_the_flag_history = Mock()
    flags = a_flag_client_reporting(True)
    flags.name = "monthly-spend-feature"
    fallback_flags = a_flag_client_reporting(True)
    fallback_flags.name = "legacy-checkout-fallback"

    a_scenario_state(flags, fallback_flags, forget_the_flag_history).reset()

    forget_the_flag_history.assert_called_once_with(
        ["monthly-spend-feature", "legacy-checkout-fallback"]
    )


def test_resetting_clears_a_flag_left_on_by_someone_else() -> None:
    # A flag left on by an abandoned run is exactly the state a reset is for.
    # Refusing to clear it because this process has no memory of staging it
    # would leave the next reader looking at an incident nobody started.
    flags = a_flag_client_reporting(True)

    a_scenario_state(flags).reset()

    flags.disable.assert_called_once()


def test_resetting_clears_a_fallback_left_off_by_someone_else() -> None:
    # The other half of an abandoned run, and the half that used to be missed.
    # A service restarted mid-incident has no memory of what it staged, so the
    # flag left the wrong way round is as likely to be the fallback as the
    # feature - and a reset that cleared only one of them left the shop broken
    # with nothing claiming to be breaking it.
    fallback_flags = a_flag_client_reporting(False)

    a_scenario_state(a_flag_client_reporting(True), fallback_flags).reset()

    fallback_flags.enable.assert_called_once()


def test_staging_survives_a_flag_the_provider_will_not_move() -> None:
    # The clearing that precedes staging is housekeeping, and housekeeping must
    # not be the reason nothing can be staged. Flags live in a provider anyone
    # can reach: a suite tidying up between cases archives the ones it did not
    # want, and the provider then refuses to toggle them. A flag that is not
    # there is not a flag left in a breaking state, so there is nothing to put
    # right - and the scenario being staged has its own flag to move.
    beyond_reach = a_flag_client_reporting(False)
    beyond_reach.enable.side_effect = FlagProviderUnavailable("archived")
    beyond_reach.disable.side_effect = FlagProviderUnavailable("archived")
    flags = a_flag_client_reporting(False)

    a_scenario_state(flags, beyond_reach).seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    flags.enable.assert_called_once()


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

    _, up_to = present(state.generated_window())

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

    _, up_to = present(state.generated_window())

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

    settle = timedelta(minutes=CLEAN_MINUTES_SHOWN_AFTER_RECOVERY)
    long_ago = utc_now() - settle - timedelta(minutes=1)
    active = present(state.active)
    state._active = replace(
        active, timeline=replace(present(active.timeline), turned_off_at=long_ago)
    )

    assert state.phase() == COMPLETE
    _, up_to = present(state.generated_window())
    assert up_to == the_first_whole_minute_after(long_ago) + settle


def test_a_revert_on_a_minute_boundary_still_leaves_its_clean_minute_behind() -> None:
    # The bucket a mitigation reads its verdict off. Measured from the revert
    # itself, the settling period ends on the same boundary it began on, and the
    # minute that would carry the recovery has nought elapsed seconds - which is
    # no reading rather than a quiet one, so the window freezes without it and an
    # action that worked is refuted for want of a measurement.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
    # The flag has to say what the timeline below says, because the timeline is
    # reconciled against the provider in both directions: a flag reading as on
    # beside a timeline recording an end is the state an agent leaves behind when
    # it puts a failed mitigation back, and it is read as the incident having
    # started again. Staged inconsistently, this test would be arranging that
    # instead of a revert.
    flags.is_enabled.return_value = False

    settle = timedelta(minutes=CLEAN_MINUTES_SHOWN_AFTER_RECOVERY)
    on_the_boundary = (utc_now() - settle - timedelta(minutes=2)).replace(
        second=0, microsecond=0
    )
    active = present(state.active)
    state._active = replace(
        active,
        timeline=replace(
            present(active.timeline),
            turned_on_at=on_the_boundary - timedelta(minutes=5),
            turned_off_at=on_the_boundary,
        ),
    )

    timeline, up_to = present(state.generated_window())
    a_span_reaching_either_side_of_the_revert = 10
    minutes = {
        minute.minute_id: minute
        for minute in generate(
            timeline, up_to, a_span_reaching_either_side_of_the_revert
        )
    }

    settled_for = CLEAN_MINUTES_SHOWN_AFTER_RECOVERY
    the_clean_minutes = [
        (the_first_whole_minute_after(on_the_boundary) + timedelta(minutes=offset))
        .strftime(TIMESTAMP_FORMAT)
        for offset in range(settled_for)
    ]
    the_broken_minute = (on_the_boundary - timedelta(minutes=1)).strftime(
        TIMESTAMP_FORMAT
    )

    # Counted, not merely looked for. A settling period measured from the revert
    # instant keeps the earlier clean minutes and loses only the last one - which
    # at a settle of a single minute is the only one there ever was.
    assert sorted(
        minute_id for minute_id in minutes if minute_id >= the_clean_minutes[0]
    ) == the_clean_minutes
    assert (
        minutes[the_clean_minutes[0]].error_rate
        < minutes[the_broken_minute].error_rate
    )


def the_clean_whole_minutes_in(state: ScenarioState, after: datetime) -> list[str]:
    """The minutes a frozen window carries that lie entirely past the revert.

    Counted off the generated window rather than derived from the settling
    period, because the settling period is the thing being checked - a count
    computed from it would agree with it however wrong it was.
    """
    timeline, up_to = present(state.generated_window())
    a_span_reaching_either_side_of_the_revert = 10
    first_clean_minute = the_first_whole_minute_after(after).strftime(TIMESTAMP_FORMAT)

    return sorted(
        minute.minute_id
        for minute in generate(
            timeline, up_to, a_span_reaching_either_side_of_the_revert
        )
        if minute.minute_id >= first_clean_minute
    )


def test_a_frozen_window_holds_enough_clean_minutes_to_confirm_a_mitigation() -> None:
    # The one thing the constant beside `_settled_at` cannot say about itself.
    # Argus reads a mitigation's verdict off the minutes a frozen window leaves
    # behind, and confirms one only from a run of clear minutes reaching its own
    # `anomaly_persistence_minutes` - two by default, and in the other repo. A
    # window that freezes holding fewer refutes every mitigation in every
    # scenario, and reads as a broken detector rather than a fixture a minute
    # short. Spelled out here rather than imported: the two repos deploy apart
    # and neither reads the other's settings, so this is the contract between
    # them and not a shared constant.
    minutes_argus_needs_to_confirm_a_recovery = 2

    # Every position in the minute, because a revert lands where it lands and the
    # arithmetic counts from the first whole minute after it - so a period
    # measured from the instant instead loses its last bucket for some of them
    # and not others, which is how this was wrong once already.
    for seconds_into_the_minute in (0, 30, 59):
        flags = a_flag_client_reporting(True)
        state = a_scenario_state(flags)
        state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])
        flags.is_enabled.return_value = False

        long_enough_ago_to_have_frozen = timedelta(
            minutes=CLEAN_MINUTES_SHOWN_AFTER_RECOVERY + 2
        )
        reverted_at = (utc_now() - long_enough_ago_to_have_frozen).replace(
            second=seconds_into_the_minute, microsecond=0
        )
        active = present(state.active)
        state._active = replace(
            active,
            timeline=replace(
                present(active.timeline),
                turned_on_at=reverted_at - timedelta(minutes=5),
                turned_off_at=reverted_at
            )
        )

        clean_minutes = the_clean_whole_minutes_in(state, after=reverted_at)

        assert state.phase() == COMPLETE, (
            f"A window reverted {seconds_into_the_minute}s into its minute and "
            f"left alone for longer than the settling period is still advancing, "
            f"so this case is not measuring a frozen window at all."
        )
        assert len(clean_minutes) >= minutes_argus_needs_to_confirm_a_recovery, (
            f"A window reverted {seconds_into_the_minute}s into its minute froze "
            f"holding {len(clean_minutes)} clean whole minutes, and Argus needs "
            f"{minutes_argus_needs_to_confirm_a_recovery} in a row before it will "
            f"confirm a mitigation. At this length every mitigation in every "
            f"scenario is refuted."
        )


def test_an_authored_scenario_has_no_timeline_to_reconcile() -> None:
    flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[BAD_DEPLOYMENT])

    assert state.timeline_now() is None


def test_seeding_the_fallback_scenario_switches_its_own_flag_off() -> None:
    # The other direction: this incident begins when a flag goes off, so
    # staging it means switching one off rather than on - and the feature flag,
    # which has nothing to do with this scenario, is left where it rests rather
    # than dragged into the incident.
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags, fallback_flags)

    state.seed(SCENARIOS[FALLBACK_DISABLED])

    assert where_it_was_left(fallback_flags) is False
    assert where_it_was_left(flags) is False


def test_seeding_the_fallback_scenario_creates_the_flag_it_stages() -> None:
    # The fallback flag is not created at startup, because a provider listing a
    # flag no staged scenario touches is a question to answer mid-demo. The
    # scenario that stages it is what brings it into existence.
    dont_care_flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(True)
    state = a_scenario_state(dont_care_flags, fallback_flags)

    state.seed(SCENARIOS[FALLBACK_DISABLED])

    fallback_flags.ensure_flag_exists.assert_called_once()
    dont_care_flags.ensure_flag_exists.assert_not_called()


def test_seeding_an_authored_scenario_creates_no_flag() -> None:
    # `bad-deployment` stages a deploy. It has no flag to bring into existence,
    # and creating one would leave the provider holding a flag nothing explains.
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(True)

    a_scenario_state(flags, fallback_flags).seed(SCENARIOS[BAD_DEPLOYMENT])

    flags.ensure_flag_exists.assert_not_called()
    fallback_flags.ensure_flag_exists.assert_not_called()


def test_the_fallback_incident_ends_when_its_flag_goes_back_on() -> None:
    # Recovery is the flag returning to the state the shop is well in, which
    # for this flag is on. A service that only understood "off means better"
    # would report this incident as still running after it was fixed.
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[FALLBACK_DISABLED])

    fallback_flags.is_enabled.return_value = True

    assert present(state.timeline_now()).turned_off_at is not None


def test_the_fallback_incident_is_still_running_while_its_flag_is_off() -> None:
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)

    state.seed(SCENARIOS[FALLBACK_DISABLED])

    assert present(state.timeline_now()).turned_off_at is None


def test_a_coincidental_flag_toggle_does_not_end_when_the_flag_is_reverted() -> None:
    # The flag really was switched on and really is not the cause. Reverting it
    # is a reasonable thing to have tried and changes nothing, which is what
    # makes the attempt refutable rather than confirmable.
    flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags)
    state.seed(SCENARIOS[FLAG_TOGGLE_RED_HERRING])

    flags.is_enabled.return_value = False

    assert present(state.timeline_now()).turned_off_at is None
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

    assert where_it_was_left(fallback_flags) is True


def test_resetting_leaves_alone_a_flag_no_scenario_staged() -> None:
    # Every flag change is evidence to whoever investigates the next incident.
    # Moving a flag this scenario never staged would plant a second suspect
    # beside the real one, and an agent that cannot tell which flag an incident
    # is about escalates instead of acting.
    #
    # Never moved *away* from where it rests is the claim, not never called:
    # staging begins by putting both flags back, and asking a flag to go where
    # it already is changes nothing and is recorded nowhere.
    flags = a_flag_client_reporting(True)
    fallback_flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    state.reset()

    fallback_flags.disable.assert_not_called()


def test_seeding_a_scenario_with_a_decoy_moves_both_flags() -> None:
    # Both changes have to reach the provider, because the provider's record of
    # what changed is the only place an investigator can find two suspects.
    flags = a_flag_client_reporting(False)
    fallback_flags = a_flag_client_reporting(True)
    state = a_scenario_state(flags, fallback_flags)

    state.seed(SCENARIOS[COMPETING_FLAG_CHANGES])

    fallback_flags.disable.assert_called()
    flags.enable.assert_called()


def test_resetting_puts_the_decoy_back_where_it_was_found() -> None:
    flags = a_flag_client_reporting(True)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[COMPETING_FLAG_CHANGES])

    state.reset()

    assert where_it_was_left(flags) is False


def test_a_reverted_decoy_is_stamped_on_the_next_read() -> None:
    # Nothing tells this service the decoy moved - the same reconciliation the
    # staged flag gets, for the flag whose movement changes nothing else.
    flags = a_flag_client_reporting(True)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[COMPETING_FLAG_CHANGES])

    flags.is_enabled.return_value = False

    assert present(state.decoy_timeline_now()).turned_off_at is not None


def test_a_decoy_put_back_where_it_broke_nothing_is_stamped_as_moved_again() -> None:
    # The scenario this reconciliation exists for. `competing-flag-changes` is
    # built so that reverting the feature flag changes nothing and has to be
    # undone - and the feature flag there is the decoy. So the one scenario that
    # makes Argus revert a decoy and put it back is the one whose decoy log line
    # is read straight off this timeline: frozen at the first revert, the shop
    # reports the decoy off while the provider says on.
    flags = a_flag_client_reporting(True)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[COMPETING_FLAG_CHANGES])

    flags.is_enabled.return_value = False
    put_back = present(state.decoy_timeline_now())

    flags.is_enabled.return_value = True
    moved_again = present(state.decoy_timeline_now())

    assert put_back.turned_off_at is not None
    assert moved_again.turned_off_at is None
    assert moved_again.turned_on_at >= put_back.turned_off_at


def test_reverting_the_decoy_leaves_the_incident_running() -> None:
    flags = a_flag_client_reporting(True)
    fallback_flags = a_flag_client_reporting(False)
    state = a_scenario_state(flags, fallback_flags)
    state.seed(SCENARIOS[COMPETING_FLAG_CHANGES])

    flags.is_enabled.return_value = False

    assert present(state.timeline_now()).turned_off_at is None
    assert state.phase() == RUNNING


def test_a_scenario_with_no_decoy_has_no_decoy_timeline() -> None:
    state = a_scenario_state(a_flag_client_reporting(True))

    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    assert state.decoy_timeline_now() is None


def a_leaking_scenario_state(flags: Mock | None = None) -> ScenarioState:
    """A state object for the scenario that touches no flag at all.

    Both clients are stubbed anyway. Staging a leak still puts the shop back
    together first, and that step asks the provider about flags whether or not
    the scenario has one.
    """
    return a_scenario_state(flags or a_flag_client_reporting(False))


def test_staging_a_leak_records_when_it_began() -> None:
    # Backdated, for the same reason a flag scenario's onset is: an audience
    # watching a flat graph for half an hour is not a demo. The difference is
    # how far back - a ramp needs a quiet opening and a climb, and both have to
    # be in the window before anybody looks.
    state = a_leaking_scenario_state()

    state.seed(SCENARIOS[RESOURCE_LEAK])

    active = state.active

    assert active is not None
    assert active.leak_started_at is not None
    assert utc_now() - active.leak_started_at >= timedelta(
        minutes=get_scenario_settings().leak_backdate_minutes
    )


def test_staging_a_leak_moves_no_flag() -> None:
    # Nothing here is a flag's doing, so nothing may look like one. A toggle
    # recorded while staging this would hand the investigation a suspect the
    # fixture invented.
    flags = a_flag_client_reporting(False)
    state = a_leaking_scenario_state(flags)

    flags.enable.reset_mock()
    state.seed(SCENARIOS[RESOURCE_LEAK])

    assert flags.enable.call_count == 0


def test_a_leak_nobody_has_restarted_is_still_running() -> None:
    state = a_leaking_scenario_state()

    state.seed(SCENARIOS[RESOURCE_LEAK])

    assert state.phase() == RUNNING


def test_a_restart_is_recorded_as_the_moment_it_happened() -> None:
    state = a_leaking_scenario_state()
    state.seed(SCENARIOS[RESOURCE_LEAK])

    restarted_at = state.restart_the_shop()

    active = state.active

    assert active is not None
    assert active.restarts == (restarted_at,)


def test_a_restart_takes_away_what_the_shop_had_accumulated() -> None:
    state = a_leaking_scenario_state()
    state.seed(SCENARIOS[RESOURCE_LEAK])
    record_visit("shopper-1", "2000")

    state.restart_the_shop()

    assert how_many_shoppers_are_remembered() == 0


def test_every_restart_is_kept_rather_than_replacing_the_last() -> None:
    # A restart has to stay in the window it happened in. A single moving
    # instant would flatten the climb before it and take the incident out of
    # the record the moment it was mitigated a second time.
    state = a_leaking_scenario_state()
    state.seed(SCENARIOS[RESOURCE_LEAK])

    first = state.restart_the_shop()
    second = state.restart_the_shop()

    active = state.active

    assert active is not None
    assert active.restarts == (first, second)


def test_a_restarted_leak_is_recovering_rather_than_running() -> None:
    state = a_leaking_scenario_state()
    state.seed(SCENARIOS[RESOURCE_LEAK])

    state.restart_the_shop()

    assert state.phase() == RECOVERING


def test_restarting_with_nothing_staged_still_clears_the_shop() -> None:
    # A platform restarts whatever is running. Refusing because this service
    # has no scenario in mind would make the control lie about what it is.
    state = a_leaking_scenario_state()
    record_visit("shopper-1", "2000")

    state.restart_the_shop()

    assert how_many_shoppers_are_remembered() == 0


def test_resetting_clears_what_the_shop_accumulated() -> None:
    state = a_leaking_scenario_state()
    state.seed(SCENARIOS[RESOURCE_LEAK])
    record_visit("shopper-1", "2000")

    state.reset()

    assert how_many_shoppers_are_remembered() == 0


def test_resetting_a_leak_moves_no_flag() -> None:
    # Nothing was staged with one, so there is nothing to put back - and a
    # toggle here would be housekeeping that the next investigation reads as
    # evidence.
    flags = a_flag_client_reporting(False)
    state = a_leaking_scenario_state(flags)
    state.seed(SCENARIOS[RESOURCE_LEAK])
    flags.enable.reset_mock()
    flags.disable.reset_mock()

    state.reset()

    assert flags.enable.call_count == 0
    assert flags.disable.call_count == 0


def an_upstream_scenario_state(flags: Mock | None = None) -> ScenarioState:
    """A state object for the scenario whose condition belongs to somebody else.

    Stubbed like the leaking one and for the same reason: staging still puts
    the shop back together first, and that step asks the provider about flags
    whether or not the scenario has any.
    """
    return a_scenario_state(flags or a_flag_client_reporting(False))


def test_staging_an_upstream_failure_records_when_the_provider_went_down() -> None:
    # Backdated the way a flag's onset is, so a diagnosable incident exists the
    # instant seeding returns.
    state = an_upstream_scenario_state()

    state.seed(SCENARIOS[UPSTREAM_DEPENDENCY_FAILURE])

    active = state.active

    assert active is not None
    assert active.provider_outage is not None
    assert utc_now() - active.provider_outage.began_at >= timedelta(
        minutes=get_scenario_settings().onset_backdate_minutes
    )


def test_staging_an_upstream_failure_moves_no_flag() -> None:
    # The fault is another company's service. A flag toggled while staging it
    # would hand the investigation a suspect inside Io.
    flags = a_flag_client_reporting(False)
    state = an_upstream_scenario_state(flags)

    flags.enable.reset_mock()
    state.seed(SCENARIOS[UPSTREAM_DEPENDENCY_FAILURE])

    assert flags.enable.call_count == 0


def test_an_upstream_failure_keeps_running_however_long_it_is_left() -> None:
    # Nothing anybody may do here ends it, so there is no recovering phase to
    # reach and no window to freeze. It runs until somebody resets it.
    state = an_upstream_scenario_state()

    state.seed(SCENARIOS[UPSTREAM_DEPENDENCY_FAILURE])

    assert state.phase() == RUNNING


def test_restarting_the_shop_does_not_end_an_upstream_failure() -> None:
    # The mitigation that answers a leak reaches nothing here: a new process
    # still cannot get an answer out of the provider.
    state = an_upstream_scenario_state()
    state.seed(SCENARIOS[UPSTREAM_DEPENDENCY_FAILURE])

    state.restart_the_shop()

    assert state.phase() == RUNNING
    active = state.active
    assert active is not None
    assert active.provider_outage is not None


def test_resetting_ends_the_outage() -> None:
    # A person deciding to stop it, which is the only thing that does.
    state = an_upstream_scenario_state()
    state.seed(SCENARIOS[UPSTREAM_DEPENDENCY_FAILURE])

    state.reset()

    assert state.active is None
    assert state.phase() == IDLE


def a_staged_cache_misconfiguration(state: ScenarioState) -> None:
    state.seed(SCENARIOS[CACHE_MISCONFIGURED])


def test_a_misconfigured_cache_is_running_until_it_is_rolled_back() -> None:
    state = a_scenario_state(a_flag_client_reporting(False))

    a_staged_cache_misconfiguration(state)

    assert state.phase() == RUNNING


def test_rolling_the_deployment_back_moves_it_into_recovering() -> None:
    # Without this the scenario reports `running` for ever: it has no flag to
    # go back and no restart to settle from, so nothing else in the phase
    # derivation can see that it ended.
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_cache_misconfiguration(state)

    state.roll_the_deployment_back()

    assert state.phase() == RECOVERING


def test_a_rollback_puts_the_shop_back_on_the_address_that_answers() -> None:
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_cache_misconfiguration(state)
    broken = present(state.active).cache_endpoint

    state.roll_the_deployment_back()

    assert present(state.active).cache_endpoint != broken
    assert present(present(state.active).cache_outage).ended_at is not None


def test_a_deployment_reconciles_itself_until_somebody_stops_it() -> None:
    state = a_scenario_state(a_flag_client_reporting(False))

    assert state.syncs_itself

    state.set_automated_sync(False)

    assert not state.syncs_itself


def test_a_reset_puts_automated_sync_back_on() -> None:
    # A run abandoned between the rollback and its undo leaves sync suspended,
    # and the next scenario would then be staged onto a deployment that
    # reconciles nothing - with its rollback accepted first time for reasons
    # belonging to the previous run.
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_cache_misconfiguration(state)
    state.set_automated_sync(False)

    state.reset()

    assert state.syncs_itself


def test_resetting_a_cache_scenario_touches_no_flag() -> None:
    # No flag staged it, so putting one back would plant a change for the next
    # investigation to find - the provider records every toggle, and an agent
    # identifies a culprit by asking what recently changed.
    flags = a_flag_client_reporting(False)
    fallback = a_flag_client_reporting(True)
    state = a_scenario_state(flags, fallback)
    a_staged_cache_misconfiguration(state)
    flags.reset_mock()
    fallback.reset_mock()

    state.reset()

    assert not flags.enable.called and not flags.disable.called
    assert not fallback.enable.called and not fallback.disable.called


def a_staged_slow_rollout(flags: Mock | None = None) -> ScenarioState:
    """A state object with the slow feature out to a few percent of traffic.

    A flag scenario like the first four, so the flag is reported on from the
    start and the staging turns it on exactly as those do. What is different is
    only what the requests it reaches cost.
    """
    state = a_scenario_state(flags or a_flag_client_reporting(True))
    state.seed(SCENARIOS[SLOW_CANARY_ROLLOUT])

    return state


def test_staging_a_slow_rollout_turns_its_flag_on() -> None:
    # A rollout is a flag somebody moved, which is the whole reason this
    # scenario costs no new mitigation: the lever that ends it is the first one
    # Argus ever had.
    flags = a_flag_client_reporting(True)

    a_staged_slow_rollout(flags)

    assert where_it_was_left(flags)


def test_staging_a_slow_rollout_puts_the_shop_on_a_working_cache() -> None:
    # Not a detail. A shop with no cache reports the baseline quantile model,
    # which has no sample for a percentile to be taken over - and the mixture
    # of a cached path and a recomputed one is what puts the 95th percentile on
    # a recomputed page, which is why the 95th does not move.
    state = a_staged_slow_rollout()

    active = state.active

    assert active is not None
    assert active.cache_endpoint == the_working_cache_endpoint()
    assert active.cache_outage is None


def test_a_slow_rollout_nobody_has_reverted_is_still_running() -> None:
    state = a_staged_slow_rollout()

    assert state.phase() == RUNNING


def test_a_restart_does_not_end_a_slow_rollout() -> None:
    # Nothing is accumulating, so there is nothing for a restart to reclaim.
    # The new process comes up reading the same flag and serving the same few
    # requests the expensive way.
    state = a_staged_slow_rollout()

    state.restart_the_shop()

    assert state.phase() == RUNNING


def test_reverting_the_flag_ends_a_slow_rollout() -> None:
    flags = a_flag_client_reporting(True)
    state = a_staged_slow_rollout(flags)

    flags.is_enabled.return_value = False

    assert state.phase() == RECOVERING


def test_resetting_a_slow_rollout_puts_its_own_flag_back() -> None:
    # Unlike the leak and the misconfiguration, this one did move a flag, so a
    # reset has one to put back - and putting it back is also what ends the
    # incident, which is why it is the only generated scenario that is resolved
    # as well as mitigated.
    flags = a_flag_client_reporting(True)
    state = a_staged_slow_rollout(flags)

    state.reset()

    assert not where_it_was_left(flags)


def a_staged_slow_dependency() -> ScenarioState:
    """A state object with the pricing service answering slowly.

    No flag, no cache and no deploy - the condition belongs to a process this
    one does not run, which is why nothing here is configured but the scenario.
    """
    state = a_scenario_state(a_flag_client_reporting(False))
    state.seed(SCENARIOS[PRICING_SERVICE_DEGRADED])

    return state


def test_a_slow_dependency_is_running_until_that_service_is_restarted() -> None:
    state = a_staged_slow_dependency()

    assert state.phase() == RUNNING


def test_restarting_the_shop_does_not_end_a_slow_dependency() -> None:
    # The whole reason the address on the action matters. Every strategy that
    # existed before this scenario would have restarted the shop, and a fixture
    # that recovered when it did would grade the wrong answer as the right one.
    state = a_staged_slow_dependency()

    state.restart_the_shop()

    assert state.phase() == RUNNING


def test_restarting_the_pricing_service_ends_it() -> None:
    state = a_staged_slow_dependency()

    state.restart_the_pricing_service()

    assert state.phase() == RECOVERING


def test_restarting_the_shop_leaves_the_pricing_services_clock_alone() -> None:
    state = a_staged_slow_dependency()
    was = state.active.pricing_serving_since if state.active else None

    state.restart_the_shop()

    assert state.active is not None
    assert state.active.pricing_serving_since == was
    assert state.active.serving_since != was


def test_restarting_the_pricing_service_leaves_the_shops_clock_alone() -> None:
    state = a_staged_slow_dependency()
    was = state.active.serving_since if state.active else None

    state.restart_the_pricing_service()

    assert state.active is not None
    assert state.active.serving_since == was
    assert state.active.pricing_serving_since != was


def test_restarting_the_pricing_service_with_nothing_staged_is_free() -> None:
    # Safe for anybody to call, exactly as the shop's own restart is: there is
    # nothing to end, and the answer is simply when they asked.
    state = a_scenario_state(a_flag_client_reporting(False))

    assert state.restart_the_pricing_service() is not None


def a_flapping_scenario() -> Scenario:
    """The scenario whose live condition is a controller, built here.

    Built rather than looked up in `SCENARIOS`, because what these cases are
    about is the autoscaler being live state - which the flag on the scenario
    selects, and the registry entry only names.
    """
    return Scenario(
        id="autoscaler-flapping",
        title="More sizes than the shop needs",
        description="a controller that will not settle",
        family=SCENARIOS[CPU_SATURATION].family,
        autoscaler_flaps=True,
    )


def a_flapping_scenario_state() -> ScenarioState:
    """A state object for the scenario that touches no flag at all.

    Both clients are stubbed anyway, for the reason the leak's are: staging still
    puts the shop back together first, and that step asks the provider about
    flags whether or not the scenario has one.
    """
    return a_scenario_state(a_flag_client_reporting(False))


def test_no_autoscaler_is_live_until_one_is_staged() -> None:
    # A live autoscaler under every scenario would scale the saturated shop out
    # on its own, and the scenario built to prove that capacity answers
    # saturation would answer itself before anybody was paged.
    state = a_flapping_scenario_state()

    assert state.autoscaler is None

    state.seed(SCENARIOS[CPU_SATURATION])

    assert state.autoscaler is None


def test_staging_a_flapping_controller_makes_one_live() -> None:
    state = a_flapping_scenario_state()

    state.seed(a_flapping_scenario())

    autoscaler = state.autoscaler

    assert autoscaler is not None
    assert autoscaler.floor < autoscaler.ceiling
    assert not autoscaler.is_pinned


def test_staging_it_stages_the_surges_traffic_and_nothing_else() -> None:
    # The surge's own condition, so the two scenarios differ in one thing rather
    # than in a figure each. And nothing else: no flag, no cache, no deploy and
    # no neighbour, because the shop is the right size for this load half the
    # time and the wrong size the rest of it.
    state = a_flapping_scenario_state()

    state.seed(a_flapping_scenario())

    active = state.active

    assert active is not None
    assert active.demand_surge is not None
    assert active.cache_outage is None
    assert active.deploy_slowdown is None
    assert active.pricing_slowdown is None
    assert active.timeline is None


def test_pinning_the_floor_holds_the_count() -> None:
    state = a_flapping_scenario_state()
    state.seed(a_flapping_scenario())
    autoscaler = state.autoscaler

    assert autoscaler is not None

    state.pin_the_autoscaler_floor_to(autoscaler.ceiling)

    pinned = state.autoscaler

    assert pinned is not None
    assert pinned.floor == pinned.ceiling
    assert pinned.is_pinned


def test_a_pin_leaves_the_minutes_already_served_alone() -> None:
    # Recorded as a moment rather than assigned, for the reason a resize is: a
    # minute already served was served under the floor in force then, and a value
    # that moved would take the flapping stretch out of the window at the instant
    # it was mitigated.
    state = a_flapping_scenario_state()
    state.seed(a_flapping_scenario())
    before = utc_now() - timedelta(minutes=5)

    state.pin_the_autoscaler_floor_to(6)

    autoscaler = state.autoscaler

    assert autoscaler is not None
    assert autoscaler.floor_during(before) == 3
    assert autoscaler.floor_during(utc_now()) == 6


def test_putting_the_floor_back_lets_the_count_move_again() -> None:
    # Both directions, because the floor is what an undo puts back. A withdrawal
    # that could only raise it would leave the controller permanently unable to
    # scale down.
    state = a_flapping_scenario_state()
    state.seed(a_flapping_scenario())
    state.pin_the_autoscaler_floor_to(6)

    state.pin_the_autoscaler_floor_to(3)

    autoscaler = state.autoscaler

    assert autoscaler is not None
    assert not autoscaler.is_pinned


def test_resetting_forgets_every_pin() -> None:
    # A run abandoned between a pin and its withdrawal would otherwise leave the
    # controller with nowhere to scale down to, and the next flapping scenario
    # would be staged onto a deployment whose count cannot move - an incident
    # that never starts.
    state = a_flapping_scenario_state()
    state.seed(a_flapping_scenario())
    state.pin_the_autoscaler_floor_to(6)

    state.reset()
    state.seed(a_flapping_scenario())

    autoscaler = state.autoscaler

    assert autoscaler is not None
    assert not autoscaler.is_pinned


def a_staged_paused_rollout(state: ScenarioState) -> None:
    state.seed(SCENARIOS[HALF_FINISHED_ROLLOUT])


def test_a_paused_rollout_splits_the_fleet_the_instant_it_is_staged() -> None:
    # Backdated like every other onset, so a diagnosable incident exists the
    # moment seeding returns rather than five minutes later.
    state = a_scenario_state(a_flag_client_reporting(False))

    a_staged_paused_rollout(state)

    assert state.active is not None
    assert state.active.paused_rollout is not None
    assert present(present(state.active).paused_rollout).ended_at is None
    assert present(present(state.active).paused_rollout).began_at < utc_now()


def test_a_paused_rollout_configures_a_cache_that_works() -> None:
    # The two revisions disagree about what an entry in that cache is, so there
    # has to be a cache for them to disagree in - and nothing is wrong with it.
    state = a_scenario_state(a_flag_client_reporting(False))

    a_staged_paused_rollout(state)

    assert state.active is not None
    assert present(state.active).cache_endpoint == the_working_cache_endpoint()
    assert state.active.cache_outage is None


def test_a_paused_rollout_is_running_until_the_deployment_is_returned() -> None:
    state = a_scenario_state(a_flag_client_reporting(False))

    a_staged_paused_rollout(state)

    assert state.phase() == RUNNING


def test_rolling_the_deployment_back_converges_the_fleet() -> None:
    # The third scenario this action ends, and the only one it ends without
    # removing anything: what a rollback buys here is every replica on one
    # version, which is a shop that works whichever version it is.
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_paused_rollout(state)

    state.roll_the_deployment_back()

    assert state.active is not None
    assert state.active.paused_rollout is not None
    assert present(present(state.active).paused_rollout).ended_at is not None
    assert state.phase() == RECOVERING


def test_a_restart_leaves_the_fleet_split() -> None:
    # The wrong answer, refuted by the fixture rather than by a rule. Nothing
    # about the mixture is the process's doing, so bringing the process back
    # changes nothing about it.
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_paused_rollout(state)

    state.restart_the_shop()

    assert state.active is not None
    assert state.active.paused_rollout is not None
    assert present(present(state.active).paused_rollout).ended_at is None
    assert state.phase() == RUNNING


def test_a_paused_rollout_stages_no_flag() -> None:
    # A page offering a flag to watch would be offering a control that changes
    # nothing, and naming one as the thing that breaks the shop would point at a
    # suspect the fixture invented.
    assert not SCENARIOS[HALF_FINISHED_ROLLOUT].stages_a_flag


def test_withdrawing_the_rollback_splits_the_fleet_again() -> None:
    # Mitigated, never resolved. The repository still declares the revision that
    # was going out, so putting the deployment back on it returns the shop to a
    # rollout stopped half-way.
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_paused_rollout(state)
    state.roll_the_deployment_back()

    state.withdraw_the_rollback()

    assert state.active is not None
    assert state.active.paused_rollout is not None
    assert present(present(state.active).paused_rollout).ended_at is None
    assert state.phase() == RUNNING


def test_a_withdrawal_starts_a_fresh_stretch_rather_than_reopening_the_old_one(
) -> None:
    # The minutes between the rollback and the withdrawal are what says the
    # rollback worked, and claiming the fleet was split throughout would erase
    # the one thing those minutes are read for.
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_paused_rollout(state)
    began = present(present(state.active).paused_rollout).began_at
    state.roll_the_deployment_back()

    state.withdraw_the_rollback()

    assert present(present(state.active).paused_rollout).began_at > began


def test_withdrawing_a_rollback_nobody_took_changes_nothing() -> None:
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_paused_rollout(state)
    began = present(present(state.active).paused_rollout).began_at

    state.withdraw_the_rollback()

    assert present(present(state.active).paused_rollout).began_at == began
    assert present(present(state.active).paused_rollout).ended_at is None


def test_withdrawing_the_rollback_makes_the_cache_unreachable_again() -> None:
    # The values file still names the wrong port, so putting the deployment back
    # on the revision that carries it puts the shop back on that port.
    state = a_scenario_state(a_flag_client_reporting(False))
    a_staged_cache_misconfiguration(state)
    assert state.active is not None
    broken = present(state.active).cache_endpoint
    state.roll_the_deployment_back()

    state.withdraw_the_rollback()

    active = state.active
    assert active is not None and active.cache_outage is not None
    assert active.cache_endpoint == broken
    assert active.cache_outage.ended_at is None
    assert state.phase() == RUNNING


def test_withdrawing_the_rollback_slows_the_shop_again() -> None:
    # The branch still holds the slower code, so putting the deployment back on
    # it makes every request pay again.
    state = a_scenario_state(a_flag_client_reporting(False))
    state.seed(SCENARIOS[BAD_DEPLOYMENT])
    assert state.active is not None and state.active.deploy_slowdown is not None
    began = state.active.deploy_slowdown.began_at
    state.roll_the_deployment_back()

    state.withdraw_the_rollback()

    active = state.active
    assert active is not None and active.deploy_slowdown is not None
    assert active.deploy_slowdown.ended_at is None
    assert active.deploy_slowdown.began_at > began
    assert state.phase() == RUNNING


def test_withdrawing_the_rollback_keeps_the_minutes_before_it() -> None:
    # The minutes the slower revision ran before the rollback are what happened,
    # and a withdrawal that forgot them would show a shop that was quick until
    # somebody took the rollback back.
    state = a_scenario_state(a_flag_client_reporting(False))
    state.seed(SCENARIOS[BAD_DEPLOYMENT])
    began = present(present(state.active).deploy_slowdown).began_at
    a_minute_before_the_rollback = (began + timedelta(minutes=1)).replace(
        second=0, microsecond=0
    )
    state.roll_the_deployment_back()

    state.withdraw_the_rollback()

    slowdown = present(present(state.active).deploy_slowdown)
    assert slowdown.share_of(a_minute_before_the_rollback, 60) == 1.0


def test_withdrawing_the_rollback_stops_the_collecting_again() -> None:
    # The port goes back to the name the scrape config does not match, and the
    # minutes nobody collected the first time stay uncollected.
    state = a_state_with_the_shop_gone_quiet()
    went_quiet = present(state.the_minute_the_shop_went_quiet())
    state.roll_the_deployment_back()

    state.withdraw_the_rollback()

    outage = present(present(state.active).scrape_outage)
    assert outage.ended_at is None
    assert outage.covers(went_quiet)


def test_withdrawing_the_rollback_drifts_the_totals_again() -> None:
    # The revision that skips the month is running again, and the stretch it
    # ran the first time still dates when it landed.
    state = a_state_with_the_drift_deployed()
    went_live = present(present(state.active).drifting_revision).turned_on_at
    state.roll_the_deployment_back()

    state.withdraw_the_rollback()

    revision = present(present(state.active).drifting_revision)
    assert revision.turned_off_at is None
    assert revision.first_turned_on_at == went_live


def a_state_with_the_totals_drifting(
    backdate_the_flag_history: Mock | None = None,
) -> ScenarioState:
    """A shop with the silent-data-corruption scenario staged.

    Its flag reports on, because that is what staging it does, and the check's
    finding is read off that flag's timeline.
    """
    state = a_scenario_state(
        a_flag_client_reporting(True),
        backdate_the_flag_history=backdate_the_flag_history,
    )
    state.seed(SCENARIOS[SILENT_DATA_CORRUPTION])

    return state


def test_the_drifting_scenario_is_backdated_by_days_rather_than_minutes() -> None:
    # The one scenario whose onset has to be outside the window the metrics cover.
    # Backdated by minutes it would be an onset a consumer could measure for
    # itself, and the whole claim of the mode - that only the data dates the fault
    # - would go untested.
    state = a_state_with_the_totals_drifting()

    age = utc_now() - present(present(state.active).timeline).turned_on_at

    assert age.days >= get_scenario_settings().drift_backdate_days


def test_staging_it_records_the_flag_change_when_the_change_happened() -> None:
    # Without this the provider's log says the flag moved a moment ago while the
    # alert says the writing went wrong a week ago, and the one piece of evidence
    # naming a cause sits outside every window anybody would look in.
    backdate = Mock()

    state = a_state_with_the_totals_drifting(backdate)

    flags, at, since = backdate.call_args.args
    assert flags == [state._flags.name]
    assert at == present(present(state.active).timeline).turned_on_at
    assert since > at


def test_staging_any_other_scenario_backdates_no_history() -> None:
    # Every other onset is minutes old, so the provider's log is already close
    # enough and a rewrite would be a fixture editing an audit trail for nothing.
    backdate = Mock()
    state = a_scenario_state(
        a_flag_client_reporting(True), backdate_the_flag_history=backdate
    )

    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    backdate.assert_not_called()


def test_the_check_finds_the_totals_the_staged_write_path_left_behind() -> None:
    state = a_state_with_the_totals_drifting()

    assert state.the_integrity_check_found().anything_disagrees


def test_the_check_finds_nothing_on_a_shop_with_nothing_staged() -> None:
    # The job is the shop's and runs whatever is going on, so it has to be able
    # to answer "nothing" - and it examines the accounts rather than skipping them,
    # which is what makes that answer evidence.
    state = a_scenario_state(a_flag_client_reporting(False))

    found = state.the_integrity_check_found()

    assert not found.anything_disagrees
    assert found.accounts_checked > 0


def test_the_check_finds_nothing_while_another_scenario_is_staged() -> None:
    # The shop has one feature flag and several scenarios behind it, so what the
    # flag is shipping is the scenario's to say. A flag turned on to break the
    # account page must not also corrupt the shop's totals.
    state = a_scenario_state(a_flag_client_reporting(True))
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    assert not state.the_integrity_check_found().anything_disagrees


def test_restarting_the_shop_leaves_the_finding_exactly_where_it_was() -> None:
    # The fault is in what was written down, so a fresh process reads back the
    # same wrong totals. Nothing else in this fixture behaves this way - a restart
    # reclaims a heap, and here it reclaims nothing.
    state = a_state_with_the_totals_drifting()
    before = state.the_integrity_check_found()

    state.restart_the_shop()

    assert state.the_integrity_check_found() == before


def a_state_with_the_drift_deployed(
    flags: Mock | None = None,
    backdate_the_flag_history: Mock | None = None,
) -> ScenarioState:
    """A shop with the deploy-caused drift staged.

    Its flag reports off, because this scenario moves none: the write path that
    skips the month is the deployed revision's, and the check reads its stretch
    off the deployment rather than off any flag.
    """
    state = a_scenario_state(
        flags or a_flag_client_reporting(False),
        backdate_the_flag_history=backdate_the_flag_history,
    )
    state.seed(SCENARIOS[MONTHLY_TOTALS_FALLING_BEHIND])

    return state


def test_the_deployed_drift_is_backdated_as_far_as_the_flag_one() -> None:
    # The same claim as the flag scenario's: an onset inside the metrics' reach
    # could be measured from the series, and only the data may date this.
    state = a_state_with_the_drift_deployed()

    age = utc_now() - present(present(state.active).drifting_revision).turned_on_at

    assert age.days >= get_scenario_settings().drift_backdate_days


def test_staging_the_deployed_drift_moves_no_flag() -> None:
    # A flag moved at the onset would make this the flag scenario with a
    # deployment beside it, and a flag revert would have something to undo.
    flags = a_flag_client_reporting(False)

    a_state_with_the_drift_deployed(flags)

    flags.enable.assert_not_called()


def test_staging_the_deployed_drift_writes_no_flag_history() -> None:
    backdate = Mock()

    a_state_with_the_drift_deployed(backdate_the_flag_history=backdate)

    backdate.assert_not_called()


def test_the_check_finds_the_totals_the_deployed_revision_left_behind() -> None:
    state = a_state_with_the_drift_deployed()

    assert state.the_integrity_check_found().anything_disagrees


def test_rolling_the_deployment_back_ends_the_write_path_s_stretch() -> None:
    # Recovery in the only terms the mode has, and the check's half of it - no
    # purchase after the end joining the drift - is `test_integrity`'s, asked of
    # the same stretch. What is asked here is that a rollback is what ends it.
    state = a_state_with_the_drift_deployed()

    rolled_back_at = state.roll_the_deployment_back()

    assert present(present(state.active).drifting_revision).turned_off_at == rolled_back_at


def test_rolling_the_deployment_back_repairs_nothing() -> None:
    state = a_state_with_the_drift_deployed()
    before = state.the_integrity_check_found()

    state.roll_the_deployment_back()

    after = state.the_integrity_check_found()
    assert after.anything_disagrees
    assert after.oldest_affected_purchase_at == before.oldest_affected_purchase_at


def test_restarting_a_shop_with_the_drift_deployed_changes_nothing() -> None:
    state = a_state_with_the_drift_deployed()
    before = state.the_integrity_check_found()

    state.restart_the_shop()

    assert state.the_integrity_check_found() == before


def a_state_with_the_shop_gone_quiet() -> ScenarioState:
    """A shop with the monitoring-blind-spot scenario staged.

    Its flags report off, because this scenario stages none: what it stages is
    a deployed revision, and the minute the collecting stopped is read off the
    scrape outage rather than off any timeline.
    """
    state = a_scenario_state(a_flag_client_reporting(False))
    state.seed(SCENARIOS[MONITORING_BLIND_SPOT])
    return state


def test_a_shop_that_stopped_publishing_reports_the_minute_it_went_quiet() -> None:
    state = a_state_with_the_shop_gone_quiet()

    assert state.the_minute_the_shop_went_quiet() is not None


def test_the_minute_reported_is_the_minute_the_deployment_landed() -> None:
    # The boundary the alert and the generator have to agree about. The alert
    # states this minute as its onset and the generator withholds it, so a
    # disagreement here would leave a consumer looking for a last row that is
    # missing, or finding one too many.
    state = a_state_with_the_shop_gone_quiet()

    assert (
        state.the_minute_the_shop_went_quiet()
        == present(present(state.active).scrape_outage).began_at.replace(second=0, microsecond=0)
    )


def test_the_scenario_that_stops_the_publishing_stages_no_flag() -> None:
    # The whole reason this was re-staged. A flag moving at the onset is
    # evidence naming a cause, and here the cause is a revision - so a flag
    # left on would put a suspect in the change channel that the incident has
    # nothing to do with.
    state = a_state_with_the_shop_gone_quiet()

    assert present(state.active).timeline is None


def test_the_minute_is_old_enough_for_a_rule_to_have_fired_on_it() -> None:
    # Staging backdates the onset so a diagnosable incident exists the instant
    # seeding returns, and for this mode that backdate is also what makes the
    # absence older than a missed scrape.
    state = a_state_with_the_shop_gone_quiet()

    quiet_for = utc_now() - present(state.the_minute_the_shop_went_quiet())

    assert quiet_for >= timedelta(minutes=2)


def test_a_shop_that_is_publishing_reports_no_such_minute() -> None:
    # Asked on every staging, so it has to be able to say nothing is wrong. The
    # shop has one flag and several scenarios behind it, and what the flag is
    # doing is the scenario's to say.
    state = a_scenario_state(a_flag_client_reporting(True))
    state.seed(SCENARIOS[FEATURE_FLAG_TOGGLE])

    assert state.the_minute_the_shop_went_quiet() is None


def test_resetting_leaves_no_minute_to_report() -> None:
    # The reset restores telemetry publishing, which for this scenario is the
    # whole of what a reset has to undo.
    state = a_state_with_the_shop_gone_quiet()

    state.reset()

    assert state.the_minute_the_shop_went_quiet() is None


def a_state_renamed_to_the_convention() -> ScenarioState:
    """A shop with the monitoring-configuration-drift scenario staged.

    The blind spot's sibling: the same silence, from a rename that was meant.
    """
    state = a_scenario_state(a_flag_client_reporting(False))
    state.seed(SCENARIOS[MONITORING_CONFIGURATION_DRIFT])
    return state


def test_the_deliberate_rename_goes_quiet_as_the_blind_spot_does() -> None:
    # The pair has to be indistinguishable everywhere but the diff, so the
    # silence is the blind spot's own: dated the same way, by the same outage.
    state = a_state_renamed_to_the_convention()

    assert (
        state.the_minute_the_shop_went_quiet()
        == present(present(state.active).scrape_outage).began_at.replace(second=0, microsecond=0)
    )


def test_the_deliberate_rename_stages_no_flag() -> None:
    state = a_state_renamed_to_the_convention()

    assert present(state.active).timeline is None


def test_restarting_the_renamed_shop_leaves_it_unread() -> None:
    # The renamed port comes back with the process, so nothing about the
    # collecting changes.
    state = a_state_renamed_to_the_convention()

    state.restart_the_shop()

    assert present(present(state.active).scrape_outage).ended_at is None


def test_rolling_the_renamed_shop_back_restores_the_collecting() -> None:
    # The scenario does not pretend a rollback fails. It works, and what makes
    # it the wrong answer is the convention it undoes.
    state = a_state_renamed_to_the_convention()

    state.roll_the_deployment_back()

    assert present(present(state.active).scrape_outage).ended_at is not None


def test_resetting_the_renamed_shop_leaves_no_minute_to_report() -> None:
    state = a_state_renamed_to_the_convention()

    state.reset()

    assert state.the_minute_the_shop_went_quiet() is None


def a_staged_categoriser_upgrade() -> ScenarioState:
    state = a_scenario_state(a_flag_client_reporting(False))
    state.seed(SCENARIOS[CATEGORISER_MODEL_UPGRADED])

    return state


def test_the_upgrade_loads_the_model_the_values_file_names() -> None:
    state = a_staged_categoriser_upgrade()

    upgrade = present(present(state.active).model_upgrade)

    assert upgrade.model == "v2"
    assert upgrade.ended_at is None


def test_the_upgrade_is_backdated_like_every_other_onset() -> None:
    before = utc_now()
    state = a_staged_categoriser_upgrade()

    began = present(present(state.active).model_upgrade).began_at

    assert began <= before - timedelta(
        minutes=get_scenario_settings().onset_backdate_minutes
    ) + timedelta(seconds=1)


def test_staging_the_upgrade_moves_no_flag() -> None:
    flags = a_flag_client_reporting(False)

    a_scenario_state(flags).seed(SCENARIOS[CATEGORISER_MODEL_UPGRADED])

    flags.enable.assert_not_called()


def test_the_upgrade_is_running_until_the_deployment_is_returned() -> None:
    assert a_staged_categoriser_upgrade().phase() == RUNNING


def test_rolling_the_deployment_back_puts_the_old_model_back() -> None:
    state = a_staged_categoriser_upgrade()

    state.roll_the_deployment_back()

    assert present(present(state.active).model_upgrade).ended_at is not None
    assert state.phase() == RECOVERING


def test_restarting_the_shop_leaves_the_upgrade_loaded() -> None:
    # The process comes back reading the same values file.
    state = a_staged_categoriser_upgrade()

    state.restart_the_shop()

    assert state.phase() == RUNNING


def test_withdrawing_the_rollback_loads_the_upgrade_again() -> None:
    state = a_staged_categoriser_upgrade()
    rolled_back_at = state.roll_the_deployment_back()

    state.withdraw_the_rollback()

    upgrade = present(present(state.active).model_upgrade)
    assert upgrade.ended_at is None
    assert [stretch.ended_at for stretch in upgrade.earlier] == [rolled_back_at]
    assert state.phase() == RUNNING
