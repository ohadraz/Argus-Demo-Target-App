from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

"""The scenarios this service can stage, and how each one produces its telemetry.

Two mechanisms sit side by side here, deliberately.

`feature-flag-toggle` is **generated**: seeding it turns a real flag on in a
real provider, and the telemetry is computed from that flag's state whenever
anyone asks. Turning the flag off ends the incident, no matter who turns it off
- which is the only way a mitigation attempt can be honestly graded.

`bad-deployment` is **authored**: a fixed list of minutes, anchored so the whole
incident sits just behind the seed instant. It has no live condition to react
to, because rolling a deployment back means pushing a commit, and nothing here
can yet be asked to do that. It stays as it was until that changes.
"""

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class ScenarioDeploy:
    """A deployment that happened during a scenario's minute.

    Only some scenarios have one - a feature flag flip is not a deploy - and
    that difference is the point: a consumer reading deploy history must be
    able to tell an incident a deploy caused from one it did not.
    """

    revision: str
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
    request_volume: int
    deploy: ScenarioDeploy | None = None


@dataclass(frozen=True)
class Scenario:
    """What a scenario is, from the outside.

    `minutes` is empty for a generated scenario, and that emptiness is what
    selects the mechanism: there is nothing authored to serve, so the generator
    is asked instead.
    """

    id: str
    title: str
    description: str
    minutes: tuple[ScenarioMinute, ...] = ()

    @property
    def is_generated(self) -> bool:
        return not self.minutes


FEATURE_FLAG_TOGGLE = "feature-flag-toggle"
BAD_DEPLOYMENT = "bad-deployment"

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
    BAD_DEPLOYMENT: Scenario(
        id=BAD_DEPLOYMENT,
        title="Bad version deployed",
        description=(
            "A deployment lands and p95 latency climbs from a 220ms baseline to "
            "timeouts, while the error rate stays mild. The deploy is recorded "
            "only in the Argo CD history - the log lines never mention it. "
            "Authored, not live: there is no rollback to perform yet."
        ),
        minutes=(
            ScenarioMinute(
                offset_minutes=0,
                # No mention of the deploy. The Argo CD channel is the only place
                # it is recorded, so a diagnosis of BAD_DEPLOYMENT can only have
                # come from there - which is what the e2e case is for.
                messages=(
                    "INFO io-shop: request succeeded",
                    "INFO io-shop: request succeeded",
                ),
                error_rate=0.01,
                p50_ms=40,
                p95_ms=220,
                request_volume=1150,
                deploy=ScenarioDeploy(
                    revision="9f4c1e7b2a3d5c8e1f0b6a4d2c9e7b5a3f1d8c6e",
                    repo_url="https://github.com/io-shop/k8s-configs",
                    path="apps/io-shop/production",
                ),
            ),
            ScenarioMinute(
                offset_minutes=1,
                messages=("WARN io-shop: p95 latency climbing",),
                error_rate=0.02,
                p50_ms=95,
                p95_ms=900,
                request_volume=1120,
            ),
            ScenarioMinute(
                offset_minutes=2,
                messages=(
                    "WARN io-shop: p95 latency at 1800ms, up from a 220ms baseline",
                ),
                error_rate=0.04,
                p50_ms=180,
                p95_ms=1800,
                request_volume=1090,
            ),
            ScenarioMinute(
                offset_minutes=3,
                messages=(
                    "ERROR io-shop: request timeout after 5000ms",
                    "ERROR io-shop: request timeout after 5000ms",
                ),
                error_rate=0.12,
                p50_ms=320,
                p95_ms=5000,
                request_volume=1050,
            ),
        ),
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
