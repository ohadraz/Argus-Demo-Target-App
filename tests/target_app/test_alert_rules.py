from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from target_app.alert_rules import (
    CATEGORISATION_CONFIDENCE_LOW,
    FIRING,
    FRAUD_HOLDS_HIGH,
    HIGH_ERROR_RATE,
    HIGH_LATENCY_P95,
    HIGH_MEMORY_USAGE,
    INACTIVE,
    LT,
    PENDING,
    SERIES_RULES,
    a_definition,
    a_rules_answer,
    last_evaluation_at,
    state_of,
)
from target_app.monitoring import FINDING_RULE_UIDS, an_alert_for, the_rule_linked_from
from target_app.prometheus import THE_STEP_SECONDS, a_matrix
from target_app.scenarios import (
    AUTOSCALER_FLAPPING,
    BAD_DEPLOYMENT,
    CATEGORISER_MODEL_UPGRADED,
    FEATURE_FLAG_TOGGLE,
    SCORER_REPLICA_RESCHEDULED,
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
               p95s: list[float] | None = None,
               confident_shares: list[float] | None = None,
               held_shares: list[float] | None = None) -> list[dict[str, Any]]:
    count = len(error_rates or p95s or confident_shares or held_shares or [])
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
            "categoriser_confident_ratio": (confident_shares or [0.9] * count)[offset],
            "fraud_held_for_review_ratio": (held_shares or [0.05] * count)[offset],
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
    assert definition["keep_firing_for"] == "0m"


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

        assert the_rule_linked_from(alert) in known


def test_the_alert_links_to_the_rule_it_is_titled_after() -> None:
    # Grafana's webhook has no field for the rule that fired; the link to the
    # rule is the one place it says, in the shape its releases build.
    alert = an_alert_for(BAD_DEPLOYMENT, WINDOW_STARTS)["alerts"][0]

    assert alert["generatorURL"].endswith(
        f"/alerting/grafana/{HIGH_LATENCY_P95.uid}/view"
    )
    assert alert["labels"]["alertname"] == HIGH_LATENCY_P95.title
    assert "ruleUID" not in alert


def test_a_rule_written_below_its_line_fires_when_the_share_falls() -> None:
    rows = minutes_of(confident_shares=[0.9] * 10 + [0.4] * 10)

    state = state_of(CATEGORISATION_CONFIDENCE_LOW, rows, at_minute(20), at_minute(20))

    assert state.state == FIRING


def test_a_rule_written_below_its_line_is_quiet_while_the_share_is_high() -> None:
    rows = minutes_of(confident_shares=[0.9] * 20)

    state = state_of(CATEGORISATION_CONFIDENCE_LOW, rows, at_minute(20), at_minute(20))

    assert state.state == INACTIVE


def test_the_confidence_rule_reads_clean_on_a_window_settled_after_a_rollback() -> None:
    # The window freezes three clean minutes after the minute a rollback lands
    # in, and that minute is half of each model. A longer range would still
    # reach back into the broken minutes, and the rule would never resolve.
    rows = minutes_of(confident_shares=[0.9] * 10 + [0.4] * 10 + [0.65] + [0.9] * 3)

    state = state_of(
        CATEGORISATION_CONFIDENCE_LOW, rows, at_minute(80), at_minute(24, 0)
    )

    assert state.state == INACTIVE


def test_a_definition_carries_its_comparator_as_grafanas_evaluator_type() -> None:
    definition = a_definition(CATEGORISATION_CONFIDENCE_LOW)

    [condition] = definition["data"][2]["model"]["conditions"]

    assert condition["evaluator"] == {"type": LT, "params": [0.8]}
    assert a_definition(HIGH_ERROR_RATE)["data"][2]["model"]["conditions"][0][
        "evaluator"
    ]["type"] == "gt"


def test_every_rules_query_is_answered_by_the_prometheus_stand_in() -> None:
    # A responder who reads a rule's definition and runs its query must be sent
    # the series the rule fired on, value for value.
    rows = minutes_of(error_rates=[0.01, 0.2, 0.3])

    for rule in SERIES_RULES.values():
        expr = a_definition(rule)["data"][0]["model"]["expr"]

        data = a_matrix(
            rows, expr, at_minute(1, 0), at_minute(3, 0), THE_STEP_SECONDS,
            now=at_minute(60), reporting_lag_minutes=0
        )

        assert data["result"], rule.uid


def test_the_memory_rule_is_evaluated_over_the_share_of_the_limit_in_use() -> None:
    rows = minutes_of(error_rates=[0.01])

    data = a_matrix(
        rows, a_definition(HIGH_MEMORY_USAGE)["data"][0]["model"]["expr"],
        at_minute(1, 0), at_minute(1, 0), THE_STEP_SECONDS,
        now=at_minute(60), reporting_lag_minutes=0
    )

    [[_, value]] = data["result"][0]["values"]
    assert float(value) == (440 * 1024**2) / (2 * 1024**3)


def test_the_categoriser_upgrade_pages_on_the_confidence_rule() -> None:
    alert = an_alert_for(CATEGORISER_MODEL_UPGRADED, WINDOW_STARTS)["alerts"][0]

    assert the_rule_linked_from(alert) == CATEGORISATION_CONFIDENCE_LOW.uid
    assert alert["labels"]["alertname"] == "CategorisationConfidenceLow"


def test_the_held_share_rule_fires_when_the_share_rises() -> None:
    rows = minutes_of(held_shares=[0.05] * 10 + [0.18] * 10)

    state = state_of(FRAUD_HOLDS_HIGH, rows, at_minute(20), at_minute(20))

    assert state.state == FIRING


def test_the_held_share_rule_reads_clean_on_a_window_settled_after_a_pin() -> None:
    # The same settled window the confidence rule has to read clean on: three
    # clean minutes after the minute the pin lands in, which is half of each.
    rows = minutes_of(held_shares=[0.05] * 10 + [0.18] * 10 + [0.11] + [0.05] * 3)

    state = state_of(FRAUD_HOLDS_HIGH, rows, at_minute(80), at_minute(24, 0))

    assert state.state == INACTIVE


def test_the_rescheduled_replica_pages_on_the_held_share_rule() -> None:
    alert = an_alert_for(SCORER_REPLICA_RESCHEDULED, WINDOW_STARTS)["alerts"][0]

    assert the_rule_linked_from(alert) == FRAUD_HOLDS_HIGH.uid
    assert alert["labels"]["alertname"] == "FraudHoldsHigh"
