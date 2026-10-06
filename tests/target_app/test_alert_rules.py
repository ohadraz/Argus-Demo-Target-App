from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from target_app.alert_rules import (
    ERROR_RATE_SUSTAINED,
    FIRING,
    HIGH_ERROR_RATE,
    HIGH_LATENCY_P95,
    INACTIVE,
    PENDING,
    SERIES_RULES,
    a_definition,
    a_rules_answer,
    last_evaluation_at,
    state_of,
)
from target_app.monitoring import FINDING_RULE_UIDS, an_alert_for
from target_app.scenarios import (
    AUTOSCALER_FLAPPING,
    BAD_DEPLOYMENT,
    FEATURE_FLAG_TOGGLE,
    SILENT_DATA_CORRUPTION,
)

"""What the shop's alert rules answer about themselves.

Argus judges a mitigation by whether the rule that paged has resolved, so the
rule's state is the verdict's evidence: firing while the shop is in the
incident, inactive once the minutes its range covers are clean, and readable
in Grafana's own shape.
"""

WINDOW_STARTS = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)


def minutes_of(error_rates: list[float] | None = None,
               p95s: list[float] | None = None) -> list[dict[str, Any]]:
    count = len(error_rates or p95s or [])
    return [
        {
            "bucket_id": (WINDOW_STARTS + timedelta(minutes=offset)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "error_rate": (error_rates or [0.01] * count)[offset],
            "p95_ms": (p95s or [190.0] * count)[offset],
            "p50_ms": 26.0,
            "p99_ms": 200.0,
            "memory_used_bytes": 440 * 1024**2,
            "memory_limit_bytes": 2 * 1024**3,
        }
        for offset in range(count)
    ]


def at_minute(minute: int, second: int = 5) -> datetime:
    return WINDOW_STARTS + timedelta(minutes=minute, seconds=second)


def test_a_rule_fires_once_its_condition_has_held_for_its_pending_period() -> None:
    rows = minutes_of(error_rates=[0.01] * 10 + [0.33] * 10)

    state = state_of(HIGH_ERROR_RATE, rows, at_minute(20), at_minute(20))

    assert state.state == FIRING


def test_a_rule_pends_while_its_condition_is_younger_than_its_pending_period() -> None:
    rows = minutes_of(error_rates=[0.01] * 10 + [0.33] * 2)

    state = state_of(HIGH_ERROR_RATE, rows, at_minute(12), at_minute(12))

    assert state.state == PENDING


def test_a_rule_is_inactive_once_the_minutes_it_reads_are_clean() -> None:
    rows = minutes_of(error_rates=[0.01] * 10 + [0.33] * 10 + [0.01] * 2)

    state = state_of(HIGH_ERROR_RATE, rows, at_minute(22), at_minute(22))

    assert state.state == INACTIVE


def test_a_settled_window_is_read_as_settled_rather_than_as_no_data() -> None:
    # The fixture freezes a scenario a few clean minutes after its fix. An
    # evaluation an hour later reads the frozen minutes, which are clean.
    rows = minutes_of(error_rates=[0.01] * 10 + [0.33] * 10 + [0.01] * 3)

    state = state_of(HIGH_ERROR_RATE, rows, at_minute(80), at_minute(23, 0))

    assert state.state == INACTIVE
    assert state.value == 0.01


def test_the_latency_rule_stays_firing_through_a_capacity_that_will_not_hold_still() -> None:
    # Two slow minutes and one quick one, over and over: the autoscaler scenario.
    # A rule reading one minute would resolve in every quick one.
    rows = minutes_of(p95s=[215.0] * 10 + [1740.0, 1740.0, 215.0] * 6)

    states = [
        state_of(HIGH_LATENCY_P95, rows, at_minute(minute), at_minute(minute)).state
        for minute in range(20, 28)
    ]

    assert states == [FIRING] * 8


def test_the_sustained_rule_stays_firing_through_a_flap_with_no_rhythm() -> None:
    # Single failing minutes with gaps of two, five, three and seven: the short
    # rule resolves in every gap, and ten minutes averaged never do.
    flap = [0.33, 0.01, 0.01, 0.33, 0.01, 0.01, 0.01, 0.01, 0.01, 0.33,
            0.01, 0.01, 0.01, 0.33, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.33]
    rows = minutes_of(error_rates=[0.33] * 10 + flap)

    sustained = {
        state_of(ERROR_RATE_SUSTAINED, rows, at_minute(m), at_minute(m)).state
        for m in range(21, 32)
    }
    short = {
        state_of(HIGH_ERROR_RATE, rows, at_minute(m), at_minute(m)).state
        for m in range(21, 32)
    }

    assert sustained == {FIRING}
    assert INACTIVE in short


def test_no_minute_in_range_is_no_data() -> None:
    state = state_of(HIGH_ERROR_RATE, [], at_minute(5), at_minute(5))

    assert state.state == INACTIVE
    assert state.value is None


def test_evaluations_land_on_whole_intervals() -> None:
    assert last_evaluation_at(HIGH_ERROR_RATE, at_minute(7, 42)) == at_minute(7, 0)


def test_a_definition_carries_its_range_in_seconds_and_its_for() -> None:
    definition = a_definition(HIGH_LATENCY_P95)

    assert definition["uid"] == HIGH_LATENCY_P95.uid
    assert definition["data"][0]["relativeTimeRange"] == {"from": 120, "to": 0}
    assert definition["for"] == "5m"
    assert definition["keepFiringFor"] == "0m"


def test_the_state_answer_is_in_grafanas_shape() -> None:
    rows = minutes_of(error_rates=[0.01] * 10 + [0.33] * 10)
    state = state_of(HIGH_ERROR_RATE, rows, at_minute(20), at_minute(20))

    answer = a_rules_answer(HIGH_ERROR_RATE.uid, "HighErrorRate", state, HIGH_ERROR_RATE)
    rule = answer["data"]["groups"][0]["rules"][0]

    assert answer["status"] == "success"
    assert rule["uid"] == HIGH_ERROR_RATE.uid
    assert rule["state"] == FIRING
    assert rule["lastEvaluation"] == "2026-10-06T10:20:00Z"
    assert rule["alerts"][0]["state"] == "Alerting"


def test_every_alert_the_shop_raises_names_a_rule_it_answers_for() -> None:
    known = set(SERIES_RULES) | set(FINDING_RULE_UIDS.values())

    for scenario in (FEATURE_FLAG_TOGGLE, BAD_DEPLOYMENT, AUTOSCALER_FLAPPING,
                     SILENT_DATA_CORRUPTION, None):
        alert = an_alert_for(scenario, WINDOW_STARTS)["alerts"][0]

        assert alert["ruleUID"] in known


def test_the_alert_names_the_rule_it_is_titled_after() -> None:
    alert = an_alert_for(BAD_DEPLOYMENT, WINDOW_STARTS)["alerts"][0]

    assert alert["ruleUID"] == HIGH_LATENCY_P95.uid
    assert alert["labels"]["alertname"] == HIGH_LATENCY_P95.title
