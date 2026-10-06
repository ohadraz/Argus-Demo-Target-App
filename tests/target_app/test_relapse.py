from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from target_app.alert_rules import (
    ERROR_RATE_SUSTAINED,
    FIRING,
    HIGH_ERROR_RATE,
    INACTIVE,
    state_of,
)
from target_app.generator import FlagTimeline, generate
from target_app.monitoring import an_alert_for, the_rule_linked_from
from target_app.relapse import GAPS, failing_minutes_after, with_relapses
from target_app.scenarios import FLAG_REVERT_LEAVES_A_FLAP, SCENARIOS

"""A revert that seems to work for a minute and then does not quite.

The scenario the backlog's one known defect is staged by: a flap with no rhythm,
which a rule reading one minute reports resolved in every quiet minute and a
rule averaging ten never does.
"""

TURNED_ON = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
REVERTED = datetime(2026, 10, 6, 10, 0, 30, tzinfo=UTC)
A_MINUTE = timedelta(minutes=1)


def test_the_first_whole_minute_after_the_revert_is_clean_and_the_next_fails() -> None:
    failing = failing_minutes_after(REVERTED, REVERTED + 30 * A_MINUTE)

    assert failing[0] == datetime(2026, 10, 6, 10, 2, tzinfo=UTC)


def test_the_gaps_between_failing_minutes_follow_the_cycle() -> None:
    failing = failing_minutes_after(REVERTED, REVERTED + 120 * A_MINUTE)
    gaps = [
        int((later - earlier) / A_MINUTE) - 1
        for earlier, later in pairwise(failing)
    ]

    assert gaps[: len(GAPS)] == list(GAPS)
    assert len(set(GAPS)) == len(GAPS)
    assert max(GAPS) < ERROR_RATE_SUSTAINED.range_minutes - 1


def test_no_failing_minute_is_staged_past_the_window() -> None:
    until = REVERTED + 10 * A_MINUTE

    assert all(minute < until for minute in failing_minutes_after(REVERTED, until))


def test_a_flag_put_back_on_ends_the_relapses_of_the_stretch_before_it() -> None:
    put_back = REVERTED + 6 * A_MINUTE
    timeline = FlagTimeline(TURNED_ON, REVERTED).again_from(put_back)

    widened = with_relapses(timeline, put_back + 30 * A_MINUTE)

    assert widened.turned_on_at == put_back
    assert widened.turned_off_at is None
    assert all(
        stretch.turned_on_at < put_back for stretch in widened.earlier
    )


def test_the_shop_flaps_after_the_revert_and_only_the_sustained_rule_sees_it() -> None:
    now = REVERTED + 40 * A_MINUTE
    timeline = with_relapses(FlagTimeline(TURNED_ON, REVERTED), now)
    rows = [
        {"bucket_id": minute.minute_id, "error_rate": minute.error_rate}
        for minute in generate(timeline, now, 120, flag="monthly-spend-feature")
    ]

    sustained = {
        state_of(ERROR_RATE_SUSTAINED, rows, REVERTED + m * A_MINUTE, now).state
        for m in range(12, 40)
    }
    short = {
        state_of(HIGH_ERROR_RATE, rows, REVERTED + m * A_MINUTE, now).state
        for m in range(12, 40)
    }

    assert sustained == {FIRING}
    assert INACTIVE in short


def test_the_scenario_pages_on_the_sustained_rule() -> None:
    alert = an_alert_for(FLAG_REVERT_LEAVES_A_FLAP, REVERTED)["alerts"][0]

    assert the_rule_linked_from(alert) == ERROR_RATE_SUSTAINED.uid
    assert SCENARIOS[FLAG_REVERT_LEAVES_A_FLAP].flaps_after_revert
