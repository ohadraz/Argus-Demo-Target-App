"""The shop's alert rules, and the state each one is in.

The half of the monitoring stack `target_app.monitoring` leaves out. That module
sends the alert; this one holds the rules the alert is about, and answers what a
Grafana-managed rule answers when asked: what it watches, over how long, how
often it is evaluated - and whether it is firing now.

Evaluated when asked rather than on a ticker, the way the shop's minutes are
generated when read. A rule's state is a function of the minutes and the instant
it is asked at, so nothing has to run between requests for the answer to be the
one a rule evaluated every interval would give.

Thresholds are absolute, as a real rule's are, and set against the shop's own
figures: far from anything a healthy minute reads, well short of anything an
incident does. Most rules fire above theirs; the one written against a share the
shop wants high fires below it. A rule whose range is longer than a minute is
longer for a reason - a scenario on that rule pauses for a minute at a time, and
a rule reading one minute would resolve in the pause.

A rule's query is PromQL the shop's Prometheus stand-in answers, and the rule is
evaluated over exactly what that query answers - see `target_app.prometheus` -
so a responder who reads a rule's definition and runs its query sees the series
the rule fired on.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any, Final

from target_app.prometheus import MEMORY_IN_USE, QUERIES, the_reading_for

# The states Grafana's Prometheus-compatible rules API reports for a rule.
FIRING: Final = "firing"
PENDING: Final = "pending"
INACTIVE: Final = "inactive"

# Where the rules live, as Grafana addresses a rule group.
FOLDER_UID: Final = "io-shop"
GROUP: Final = "io-shop-slos"

# How a threshold compares, as Grafana's threshold expression names its
# evaluator types.
GT: Final = "gt"
LT: Final = "lt"

_COMPARISONS: Final[Mapping[str, Callable[[float, float], bool]]] = {
    GT: operator.gt,
    LT: operator.lt,
}

_A_MINUTE: Final = timedelta(minutes=1)


@dataclass(frozen=True)
class AlertRule:
    """One rule: what it reads, over how long, and the line it fires across.

    `reduce` is how the minutes in the range become one figure - Grafana's
    Reduce expression. `comparator` is which side of the threshold the rule
    fires on, in Grafana's evaluator vocabulary. `pending` is the rule's `for`:
    how long the condition has to hold before the rule fires rather than pends.
    """

    uid: str
    title: str
    query: str
    reduce: str
    threshold: float
    range_minutes: int
    comparator: str = GT
    pending: timedelta = timedelta(0)
    keep_firing_for: timedelta = timedelta(0)
    interval: timedelta = _A_MINUTE


@dataclass(frozen=True)
class RuleState:
    """What a rule reads as at one instant.

    `active_at` is when the run of evaluations it is currently firing or pending
    on began, and `None` while it is inactive.
    """

    state: str
    last_evaluation: datetime
    active_at: datetime | None
    value: float | None


_REDUCERS: Final[Mapping[str, Callable[[Sequence[float]], float]]] = {
    "last": lambda values: values[-1],
    "mean": mean,
    "max": max,
}

HIGH_ERROR_RATE: Final = AlertRule(
    uid="io-shop-high-error-rate",
    title="HighErrorRate",
    query=QUERIES["error_rate"],
    reduce="last",
    # A healthy minute reads four percent at worst; the least of any incident
    # on this rule reads fifteen.
    threshold=0.10,
    range_minutes=1,
    pending=timedelta(minutes=5)
)
# Two minutes, and the maximum over them, because one scenario on this rule is a
# capacity that will not hold still: two slow minutes, one quick one, over and
# over. A rule reading the last minute resolves in every quick one.
HIGH_LATENCY_P95: Final = AlertRule(
    uid="io-shop-high-latency-p95",
    title="HighLatency",
    query=QUERIES["p95_ms"],
    reduce="max",
    threshold=600.0,
    range_minutes=2,
    pending=timedelta(minutes=5)
)
HIGH_LATENCY_P50: Final = AlertRule(
    uid="io-shop-high-latency-p50",
    title="HighLatency",
    query=QUERIES["p50_ms"],
    reduce="last",
    threshold=100.0,
    range_minutes=1,
    pending=timedelta(minutes=5)
)
# The maximum over two minutes for a different reason: the tail of a canary
# serving three requests in a hundred is the third-slowest of six, and an
# occasional minute draws six quick ones.
HIGH_LATENCY_P99: Final = AlertRule(
    uid="io-shop-high-latency-p99",
    title="HighLatency",
    query=QUERIES["p99_ms"],
    reduce="max",
    threshold=500.0,
    range_minutes=2,
    pending=timedelta(minutes=5)
)
HIGH_MEMORY_USAGE: Final = AlertRule(
    uid="io-shop-high-memory-usage",
    title="HighMemoryUsage",
    query=MEMORY_IN_USE,
    reduce="last",
    threshold=0.6,
    range_minutes=1,
    pending=timedelta(minutes=15)
)
# The long-window rule: the error rate averaged over ten minutes, against a
# lower line. What it sees that the rule above cannot is a shop failing one
# minute in a few - each failing minute sets the short rule pending and each
# quiet one clears it, so it never fires, where ten minutes averaged stay above
# the line while any one of them fails. Two and a
# half percent because one failing minute in ten averages above three, and ten
# healthy minutes average one, two at the very worst.
ERROR_RATE_SUSTAINED: Final = AlertRule(
    uid="io-shop-error-rate-sustained",
    title="ErrorRateSustained",
    query=QUERIES["error_rate"],
    reduce="mean",
    threshold=0.025,
    range_minutes=10
)
# The one rule here that fires below its line, because it watches a share the
# shop wants high: how much of what was bought the categoriser filed with
# confidence. A healthy minute files nine purchases in ten; a model that has
# stopped recognising the catalogue files well under half. Averaged over two
# minutes rather than read off one, as a ratio over a sample is - and no longer
# than two, so that a shop whose categoriser has been put back reads clean on
# the minutes a settled window still shows.
CATEGORISATION_CONFIDENCE_LOW: Final = AlertRule(
    uid="io-shop-categorisation-confidence-low",
    title="CategorisationConfidenceLow",
    query=QUERIES["categoriser_confident_ratio"],
    reduce="mean",
    threshold=0.8,
    comparator=LT,
    range_minutes=2,
    pending=timedelta(minutes=5)
)
# A share the shop wants low: how much of what was bought the fraud scorer held
# for somebody to look at. A healthy minute holds about one purchase in twenty,
# the dearest few; a scorer whose arithmetic has stopped meaning anything holds
# about half of whatever it scores. One replica of three doing that lifts the
# minute to near one in five, so a line at one in ten is clear of both. Two
# minutes, for the reason the categoriser's rule is two.
FRAUD_HOLDS_HIGH: Final = AlertRule(
    uid="io-shop-fraud-holds-high",
    title="FraudHoldsHigh",
    query=QUERIES["fraud_held_for_review_ratio"],
    reduce="mean",
    threshold=0.1,
    range_minutes=2,
    pending=timedelta(minutes=5)
)

SERIES_RULES: Final[Mapping[str, AlertRule]] = {
    rule.uid: rule
    for rule in (
        HIGH_ERROR_RATE,
        HIGH_LATENCY_P95,
        HIGH_LATENCY_P50,
        HIGH_LATENCY_P99,
        HIGH_MEMORY_USAGE,
        ERROR_RATE_SUSTAINED,
        CATEGORISATION_CONFIDENCE_LOW,
        FRAUD_HOLDS_HIGH,
    )
}


def last_evaluation_at(rule: AlertRule, now: datetime) -> datetime:
    """The most recent instant the rule was evaluated at: the latest whole
    interval at or before `now`."""
    seconds = int(rule.interval.total_seconds())
    stamp = int(now.timestamp())

    return datetime.fromtimestamp(stamp - stamp % seconds, UTC)


def state_of(rule: AlertRule,
             rows: Sequence[Mapping[str, Any]],
             now: datetime,
             window_ends_at: datetime) -> RuleState:
    """What `rule` reads as at `now`, over the shop's minutes `rows`.

    Firing once its condition has held at every evaluation across its pending
    period, pending while it has held for less, inactive the first evaluation it
    does not - this shop's rules keep firing for no longer than that.

    `window_ends_at` is the instant the shop's minutes run up to. It is `now`
    while a scenario is live, and earlier once one has settled and its window
    froze; an evaluation after that reads the settled shop - the minutes up to
    the freeze - rather than minutes nobody generated, which would read as
    inactive.
    """
    evaluated_at = last_evaluation_at(rule, now)

    def figure_at(moment: datetime) -> float | None:
        return _the_figure(rule, rows, min(moment, window_ends_at))

    def holds_at(moment: datetime) -> bool:
        figure = figure_at(moment)

        return figure is not None and _COMPARISONS[rule.comparator](
            figure, rule.threshold
        )

    value = figure_at(evaluated_at)

    if not holds_at(evaluated_at):
        return RuleState(INACTIVE, evaluated_at, None, value)

    # Back only as far as the pending period, which is all the answer turns on.
    began = evaluated_at

    while began - evaluated_at > -rule.pending and holds_at(began - rule.interval):
        began -= rule.interval

    return RuleState(
        FIRING if evaluated_at - began >= rule.pending else PENDING,
        evaluated_at,
        began,
        value
    )


def _the_figure(rule: AlertRule,
                rows: Sequence[Mapping[str, Any]],
                at: datetime) -> float | None:
    """The rule's reduced figure at one evaluation, or `None` with no data:
    over the minutes that had ended by `at` and began within the range."""
    reading = the_reading_for(rule.query)

    if reading is None:
        raise ValueError(f"no series answers the query of rule {rule.uid}")

    values = [
        value
        for value in (
            reading(row)
            for row in rows
            if at - rule.range_minutes * _A_MINUTE
            <= _the_minute_of(row)
            <= at - _A_MINUTE
        )
        if value is not None
    ]

    return _REDUCERS[rule.reduce](values) if values else None


def _the_minute_of(row: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(row["bucket_id"])


def a_definition(rule: AlertRule) -> dict[str, Any]:
    """The rule in the shape Grafana's provisioning API answers
    `GET /api/v1/provisioning/alert-rules/{uid}` with."""
    seconds = rule.range_minutes * 60

    return {
        "uid": rule.uid,
        "title": rule.title,
        "folderUID": FOLDER_UID,
        "ruleGroup": GROUP,
        "condition": "C",
        "for": _a_duration(rule.pending),
        "keep_firing_for": _a_duration(rule.keep_firing_for),
        "noDataState": "NoData",
        "execErrState": "Error",
        "data": [
            {
                "refId": "A",
                "relativeTimeRange": {"from": seconds, "to": 0},
                "datasourceUid": "prometheus",
                "model": {"expr": rule.query, "refId": "A"},
            },
            {
                "refId": "B",
                "relativeTimeRange": {"from": seconds, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "type": "reduce", "reducer": rule.reduce, "expression": "A",
                    "refId": "B"
                },
            },
            {
                "refId": "C",
                "relativeTimeRange": {"from": seconds, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "type": "threshold",
                    "expression": "B",
                    "conditions": [
                        {
                            "evaluator": {
                                "type": rule.comparator, "params": [rule.threshold]
                            }
                        }
                    ],
                    "refId": "C",
                },
            },
        ],
    }


def a_rule_group(rules: Sequence[AlertRule]) -> dict[str, Any]:
    """The group in the shape
    `GET /api/v1/provisioning/folder/{folderUid}/rule-groups/{group}` answers."""
    return {
        "title": GROUP,
        "folderUid": FOLDER_UID,
        "interval": int(_A_MINUTE.total_seconds()),
        "rules": [a_definition(rule) for rule in rules],
    }


def a_rules_answer(rule_uid: str,
                   title: str,
                   state: RuleState,
                   rule: AlertRule | None = None) -> dict[str, Any]:
    """One rule's state in the shape Grafana's Prometheus-compatible
    `GET /api/prometheus/grafana/api/v1/rules` answers."""
    stamp = state.last_evaluation.strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts = (
        []
        if state.state == INACTIVE
        else [
            {
                "labels": {"alertname": title, "service": "io-shop"},
                "annotations": {},
                "state": "Alerting" if state.state == FIRING else "Pending",
                "activeAt": (
                    state.active_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if state.active_at is not None
                    else None
                ),
                "value": "" if state.value is None else str(state.value),
            }
        ]
    )

    return {
        "status": "success",
        "data": {
            "groups": [
                {
                    "name": GROUP,
                    "file": FOLDER_UID,
                    "interval": int(_A_MINUTE.total_seconds()),
                    "rules": [
                        {
                            "uid": rule_uid,
                            "name": title,
                            "state": state.state,
                            "health": "ok",
                            "type": "alerting",
                            "lastEvaluation": stamp,
                            "duration": (
                                int(rule.pending.total_seconds()) if rule else 0
                            ),
                            "keepFiringFor": (
                                int(rule.keep_firing_for.total_seconds())
                                if rule
                                else 0
                            ),
                            "alerts": alerts,
                        }
                    ],
                }
            ]
        },
    }


def _a_duration(span: timedelta) -> str:
    seconds = int(span.total_seconds())

    return f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds}s"
