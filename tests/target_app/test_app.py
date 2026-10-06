from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from statistics import median
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from io_shop.visits import (
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
)
from target_app import app as app_module
from target_app.app import (
    AN_INVALID_REQUEST,
    AUTOSCALER_GROUP,
    AUTOSCALER_KIND,
    AUTOSCALER_VERSION,
    A_REQUEST_THE_PROVIDER_REFUSES,
    GENERATED_SPAN_MINUTES,
    MERGE_PATCH_TYPE,
    MIN_REPLICAS_FIELD,
    REPLICAS_PARAMETER,
    RESTART_ACTION,
    SCALE_ACTION,
    SPEC_FIELD,
    THE_LARGEST_PAGE,
    THE_PAGE_NOBODY_ASKED_FOR,
    THE_SMALLEST_PAGE,
    app,
    to_bucket_id,
)
from target_app.flags import FlagClient
from target_app.generator import BASELINE_MEMORY_BYTES, SETTLED_UPTIME
from target_app.monitoring import an_alert_for, the_rule_for
from target_app.prometheus import QUERIES
from target_app.scenarios import (
    AUTOSCALER_FLAPPING,
    CACHE_MISCONFIGURED,
    CONTROL_PLANE_UNREACHABLE,
    CPU_SATURATION,
    HALF_FINISHED_ROLLOUT,
    MONITORING_CONFIGURATION_DRIFT,
    MONTHLY_STATEMENT_PANEL,
    MONTHLY_TOTALS_FALLING_BEHIND,
    PRICING_SERVICE_DEGRADED,
    RESOURCE_LEAK,
    SILENT_DATA_CORRUPTION,
    SLOW_CANARY_ROLLOUT,
    THE_COMMIT_THAT_NAMED_EVERY_PORT_FOR_ITS_PROTOCOL,
    THE_COMMIT_THAT_STOPPED_CARRYING_THE_MONTH,
    TIMESTAMP_FORMAT,
    UPSTREAM_DEPENDENCY_FAILURE,
)
from target_app.settings import the_deployed_replica_count
from target_app.state import ScenarioState

"""The service's own endpoints, asked the way anybody actually asks them.

The leak is the scenario worth driving from out here rather than through the
state object. Its whole point is that the same restart happens whoever asks for
it, and "whoever" means two different routes into this file - the console's own
control, and the shape a deployment platform would use. A test that called the
state object directly would prove neither of them reaches it.

No lifespan: it waits for a real flag provider, and nothing here stages a flag.
"""

RESTART_ACTION_PATH = "/argocd/io-shop/resource/actions/v2"


def _the_catalogs_own_clients_replaced_by(
    flags: FlagClient, fallback_flags: FlagClient
) -> tuple[FlagClient, FlagClient]:
    """Points the two clients the catalogue reads at these, and answers the two
    it was reading.

    The state object is not the only way into the provider: the catalogue reads
    both flags through the module's own clients, to report their live state
    whatever is staged. Replacing the state alone left those aimed at a provider
    no test here runs, so every catalogue request spent two four-second failed
    connects to answer `None` - which is the same answer an unreachable provider
    is supposed to give, so the suite was correct and two and a half times
    slower, and stayed that way until somebody timed it.
    """
    was = app_module.flags, app_module.fallback_flags
    app_module.flags, app_module.fallback_flags = flags, fallback_flags

    return was


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The service with a stubbed provider behind it, for one test.

    The app builds its state at import, so it is replaced rather than
    configured: what these cases are about is what the endpoints do, and a
    scenario staged over a real provider would be about the provider.
    """
    flags = Mock(spec=FlagClient)
    flags.is_enabled.return_value = False
    flags.name = "monthly-spend-feature"
    fallback_flags = Mock(spec=FlagClient)
    fallback_flags.is_enabled.return_value = True
    fallback_flags.name = "legacy-checkout-fallback"

    was = app_module.state
    # Both of the provider's history seams stubbed, for one reason: each reaches
    # the provider's own database, which no test here has. One clears the log on a
    # reset; the other backdates a staging toggle to when the change it stages
    # actually happened.
    app_module.state = ScenarioState(flags, fallback_flags, Mock(), Mock())
    was_read_directly = _the_catalogs_own_clients_replaced_by(flags, fallback_flags)
    forget_every_visit()

    yield TestClient(app)

    app_module.state = was
    _the_catalogs_own_clients_replaced_by(*was_read_directly)
    forget_every_visit()


def a_staged_leak(client: TestClient) -> None:
    seeded = client.post("/scenario/seed", json={"scenario_id": RESOURCE_LEAK})

    assert seeded.status_code == 200


def the_newest_minute(client: TestClient) -> dict:
    """The bucket covering the minute the shop is in, once there is one.

    Waits for it, for exactly the reason `the_minute_of` below waits: the minute
    in progress is reported only once a whole second of it has elapsed, so a read
    landing in the first second of a minute answers with the minute *before* it -
    a bucket describing the shop as it was before whatever this test just did.

    That bucket is right and asserting an action against it is wrong. It bit the
    scale-out cases the same way it bit the restart cases, and in the same
    proportion: `test_putting_the_count_back_returns_the_shop_to_saturation` read
    a stale minute as its relieved baseline, found it already saturated, and
    failed against a figure that had never moved.
    """
    deadline = time.monotonic() + A_MINUTE_BECOMES_READABLE_SECONDS
    this_minute = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")

    while True:
        buckets = client.get("/scenario/metrics").json()

        assert buckets

        if buckets[-1]["bucket_id"].startswith(this_minute):
            return buckets[-1]

        assert time.monotonic() < deadline, (
            f"no bucket covers {this_minute}, the newest being "
            f"{buckets[-1]['bucket_id']}"
        )
        # A read that crossed a minute boundary is asking about a minute that has
        # now passed, and waiting for it would wait for ever.
        this_minute = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")


def the_minute_of(client: TestClient, restarted: Response) -> dict:
    """The bucket covering the minute the shop came back in, once there is one.

    Not simply the newest bucket, and not read on the first try. The minute in
    progress is reported only once a whole second of it has elapsed - see
    `generator.generate`, which says why a zero-second minute is no reading at
    all - so a restart landing in the first second of a minute is not in any
    bucket yet, and the newest one describes the minute *before* it, whose heap
    had not been reclaimed because at that moment it had not been.

    That bucket is right and asserting a restart against it is wrong, which is
    a failure about once in every sixty runs. So this waits for the minute the
    restart actually happened in, which is at most the second the shop needs
    before it will report it.
    """
    came_back = restarted.json()["restarted_at"][:len("0000-00-00T00:00")]
    deadline = time.monotonic() + A_MINUTE_BECOMES_READABLE_SECONDS

    while True:
        buckets = client.get("/scenario/metrics").json()
        covering = [
            bucket for bucket in buckets if bucket["bucket_id"].startswith(came_back)
        ]

        if covering:
            return covering[-1]

        assert time.monotonic() < deadline, (
            f"no bucket covers {came_back}, the newest being "
            f"{buckets[-1]['bucket_id']}"
        )


# How long the shop may take to start reporting the minute it is in. One whole
# second of a minute has to elapse before it is a reading, and a little more
# than that covers the request that asks.
A_MINUTE_BECOMES_READABLE_SECONDS = 3.0

# An hour, in the unix seconds the provider's window arrives as. Any hour: what
# these cases are about is the page, and the shop trades through every minute.
SOME_WINDOW_START = 1_788_436_800
SOME_WINDOW_END = SOME_WINDOW_START + 3_600


def _charges_in_some_window(client: TestClient, **asked: object) -> Response:
    return client.get(
        "/stripe/v1/charges",
        params={
            "created[gte]": SOME_WINDOW_START,
            "created[lte]": SOME_WINDOW_END,
            **asked,
        },
    )


def test_a_page_nobody_sized_holds_what_the_provider_gives_unasked(
    client: TestClient
) -> None:
    # Ten, which is the real service's default. A stand-in answering its own
    # maximum instead would hide from an adapter that it has to ask - and the
    # adapter that forgot would then read a tenth of every window in production
    # and none of that here.
    answered = _charges_in_some_window(client)

    assert len(answered.json()["data"]) == THE_PAGE_NOBODY_ASKED_FOR


def test_the_largest_page_the_provider_serves_is_served(client: TestClient) -> None:
    answered = _charges_in_some_window(client, limit=THE_LARGEST_PAGE)

    assert len(answered.json()["data"]) == THE_LARGEST_PAGE


def test_a_page_larger_than_the_provider_serves_is_refused(
    client: TestClient
) -> None:
    # The round trips a week of charges costs are real, and asking for a bigger
    # page is the first fix anybody reaches for. It is not available: the real
    # service caps this at a hundred, so a stand-in that quietly served more
    # would bless a fix that fails on the first live account it meets.
    refused = _charges_in_some_window(client, limit=THE_LARGEST_PAGE + 1)

    assert refused.status_code == A_REQUEST_THE_PROVIDER_REFUSES
    assert refused.json()["error"]["param"] == "limit"


def test_a_page_of_nothing_is_refused(client: TestClient) -> None:
    # The other end of the same rule. A page of none reads as a shop that took
    # nothing, which is the one answer this endpoint must never give by accident.
    refused = _charges_in_some_window(client, limit=THE_SMALLEST_PAGE - 1)

    assert refused.status_code == A_REQUEST_THE_PROVIDER_REFUSES
    assert refused.json()["error"]["type"] == AN_INVALID_REQUEST


def test_the_leak_is_seedable_by_id(client: TestClient) -> None:
    a_staged_leak(client)

    assert client.get("/scenario/status").json()["active_scenario"] == RESOURCE_LEAK


def test_the_status_says_when_the_scenario_was_seeded(client: TestClient) -> None:
    # The instant the whole window hangs off: the minutes are numbered back from
    # it and the deploy history is dated against it. A consumer replaying a
    # recorded walk lines that walk's frozen timestamps up against this, so an
    # absent one leaves it guessing at the anchor this service actually used.
    a_staged_leak(client)

    seeded_at = client.get("/scenario/status").json()["seeded_at"]

    assert seeded_at is not None
    assert (
        datetime.now(UTC) - datetime.fromisoformat(seeded_at)
    ).total_seconds() < A_MINUTE_BECOMES_READABLE_SECONDS


def test_a_shop_with_nothing_staged_has_no_seeding_to_date(client: TestClient) -> None:
    # The same answer an absent scenario gives, and for the same reason: there
    # is no seeding to date anything from, and a moment reported here would be
    # one nothing in the window was built against.
    client.post("/scenario/reset")

    assert client.get("/scenario/status").json()["seeded_at"] is None


def test_seeding_says_which_rule_the_scenario_trips(client: TestClient) -> None:
    seeded = client.post("/scenario/seed", json={"scenario_id": RESOURCE_LEAK})

    assert seeded.json()["rule_uid"] == the_rule_for(RESOURCE_LEAK).uid


def test_a_shop_with_nothing_staged_names_no_rule(client: TestClient) -> None:
    client.post("/scenario/reset")

    assert client.get("/scenario/status").json()["rule_uid"] is None


def test_the_leak_is_offered_in_the_console(client: TestClient) -> None:
    # It was hidden while the catalog badged a flag for any generated scenario,
    # which named a suspect for an incident no flag is part of. The catalog
    # asks whether a scenario stages a flag now, so there is nothing left to
    # hide it from.
    offered = [
        entry["id"] for entry in client.get("/scenario/catalog").json()["scenarios"]
    ]

    assert RESOURCE_LEAK in offered


def test_the_leak_offers_no_flag_to_watch(client: TestClient) -> None:
    # A heap climbing is nobody's toggle. A badge here would be a control that
    # changes nothing, beside an incident no flag can end.
    catalog = client.get("/scenario/catalog").json()
    leak = next(
        entry for entry in catalog["scenarios"] if entry["id"] == RESOURCE_LEAK
    )

    assert leak["flags"] == []


def test_a_leaking_shop_reports_a_heap_above_its_baseline(client: TestClient) -> None:
    a_staged_leak(client)

    assert the_newest_minute(client)["memory_used_bytes"] > BASELINE_MEMORY_BYTES * 1.5


def test_a_leaking_shop_reports_the_limit_it_is_climbing_towards(
    client: TestClient,
) -> None:
    # Without the limit the figure says nothing: 1.3GiB is a crisis in one
    # container and a quiet afternoon in another.
    a_staged_leak(client)

    minute = the_newest_minute(client)

    assert minute["memory_limit_bytes"] > minute["memory_used_bytes"]


def test_the_shop_says_what_its_heap_is_doing(client: TestClient) -> None:
    a_staged_leak(client)

    assert "heap at" in " ".join(client.get("/logs").json())


def test_restarting_through_the_console_reclaims_the_heap(client: TestClient) -> None:
    a_staged_leak(client)

    restarted = client.post("/scenario/restart")

    assert restarted.status_code == 200
    assert the_minute_of(client, restarted)["memory_used_bytes"] < (
        BASELINE_MEMORY_BYTES * 1.2
    )


def test_restarting_through_the_console_says_when(client: TestClient) -> None:
    a_staged_leak(client)

    restarted = client.post("/scenario/restart")

    assert restarted.json()["restarted_at"] is not None


def test_restarting_takes_away_what_the_shop_had_accumulated(
    client: TestClient,
) -> None:
    a_staged_leak(client)
    record_visit("shopper-1", "2000")

    client.post("/scenario/restart")

    assert how_many_shoppers_are_remembered() == 0


def test_a_restart_moves_the_process_the_newest_minute_reports(
    client: TestClient,
) -> None:
    # The only evidence a restart landed. Memory falling is ambiguous on its
    # own - the process restarted, or the traffic dropped - and a caller about
    # to judge whether the leak was reclaimed has to know which.
    a_staged_leak(client)
    was_serving = the_newest_minute(client)["process_start_time_seconds"]

    client.post("/scenario/restart")

    assert the_newest_minute(client)["process_start_time_seconds"] > was_serving


def test_the_platform_restarts_the_same_shop_the_console_does(
    client: TestClient,
) -> None:
    # The point of the whole control. A mitigation that behaved differently
    # depending on who asked for it would be a fixture grading itself.
    a_staged_leak(client)

    ran = client.post(
        RESTART_ACTION_PATH,
        json={
            "namespace": "production",
            "resourceName": "io-shop",
            "group": "apps",
            "kind": "Deployment",
            "action": RESTART_ACTION,
        },
    )

    assert ran.status_code == 200
    assert the_newest_minute(client)["memory_used_bytes"] < BASELINE_MEMORY_BYTES * 1.2


def test_the_platform_refuses_an_action_it_does_not_run(client: TestClient) -> None:
    # A platform answering 200 to an action it did not run would have a caller
    # believe production had changed when it had not.
    refused = client.post(RESTART_ACTION_PATH, json={"action": "delete"})

    assert refused.status_code == 400


def test_the_platform_restart_needs_nothing_but_the_action(client: TestClient) -> None:
    # Every other field is the vendor's, and this shop has one service and no
    # namespaces. Requiring them would make the stand-in stricter than the
    # thing it stands in for.
    a_staged_leak(client)

    ran = client.post(RESTART_ACTION_PATH, json={"action": RESTART_ACTION})

    assert ran.status_code == 200


def a_staged_upstream_failure(client: TestClient) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": UPSTREAM_DEPENDENCY_FAILURE}
    )

    assert seeded.status_code == 200


def test_the_upstream_failure_is_offered_in_the_console(client: TestClient) -> None:
    # Worth watching: it is the scenario where the right answer is that Argus
    # does nothing, and an audience seeing that happen is the point of showing
    # it at all.
    offered = [
        entry["id"] for entry in client.get("/scenario/catalog").json()["scenarios"]
    ]

    assert UPSTREAM_DEPENDENCY_FAILURE in offered


def test_the_upstream_failure_offers_no_flag_to_watch(client: TestClient) -> None:
    # No flag is in play, so none may be badged. A page naming one would be
    # pointing an audience at a suspect the fixture invented, and offering a
    # control that changes nothing.
    catalog = client.get("/scenario/catalog").json()
    upstream = next(
        entry for entry in catalog["scenarios"]
        if entry["id"] == UPSTREAM_DEPENDENCY_FAILURE
    )

    assert upstream["flags"] == []


def test_an_upstream_failure_fails_the_shops_account_pages(client: TestClient) -> None:
    a_staged_upstream_failure(client)

    assert the_newest_minute(client)["error_rate"] > 0.2


def test_an_upstream_failure_leaves_the_heap_where_it_was(client: TestClient) -> None:
    # Flat memory is half of what tells this apart from a leak, and the shop
    # has to report it that way for the distinction to be readable at all.
    a_staged_upstream_failure(client)

    assert the_newest_minute(client)["memory_used_bytes"] < BASELINE_MEMORY_BYTES * 1.5


def test_restarting_the_shop_leaves_an_upstream_failure_failing(
    client: TestClient,
) -> None:
    # The mitigation that answers a leak reaches nothing here: a new process
    # still cannot get an answer out of the provider. This is the assertion
    # that keeps the scenario gradeable - an agent that restarted to see what
    # would happen is told, by the telemetry, that nothing happened.
    a_staged_upstream_failure(client)

    restarted = client.post("/scenario/restart")

    assert restarted.status_code == 200
    assert the_newest_minute(client)["error_rate"] > 0.2


def test_resetting_ends_the_outage(client: TestClient) -> None:
    # The only thing that ends it, and it is a person's doing rather than
    # Argus's. A shop with nothing staged serves no telemetry at all - which is
    # how every scenario here ends, not something about this one.
    a_staged_upstream_failure(client)

    reset = client.post("/scenario/reset")

    assert reset.status_code == 200
    assert client.get("/scenario/status").json()["active_scenario"] is None
    assert client.get("/scenario/metrics").json() == []


def a_staged_cache_misconfiguration(client: TestClient) -> None:
    seeded = client.post("/scenario/seed", json={"scenario_id": CACHE_MISCONFIGURED})

    assert seeded.status_code == 200


def suspend_automated_sync(client: TestClient) -> None:
    client.put("/argocd/io-shop/spec", json={"syncPolicy": {}})


def test_the_cache_scenario_is_seedable_by_id(client: TestClient) -> None:
    a_staged_cache_misconfiguration(client)

    assert client.get("/scenario/status").json()["active_scenario"] == CACHE_MISCONFIGURED


def test_a_generated_scenario_can_carry_a_deploy(client: TestClient) -> None:
    # Being generated is not an answer to whether anything was deployed. This
    # one's cause *is* a deploy, and a history that reported none would hide
    # the only evidence that names it.
    a_staged_cache_misconfiguration(client)

    history = client.get("/argocd/io-shop").json()["status"]["history"]

    assert len(history) == 2


def test_a_generated_scenario_staging_no_deploy_still_reports_none(
    client: TestClient
) -> None:
    a_staged_leak(client)

    assert client.get("/argocd/io-shop").json()["status"]["history"] == []


def test_the_application_reports_that_it_syncs_itself(client: TestClient) -> None:
    # A GitOps deployment reconciles itself unless somebody stopped it, and a
    # rollback cannot run while it does.
    a_staged_cache_misconfiguration(client)

    spec = client.get("/argocd/io-shop").json()["spec"]

    assert spec["syncPolicy"]["automated"] is not None


def test_a_rollback_is_refused_while_the_application_syncs_itself(
    client: TestClient
) -> None:
    # What the real platform does, and the fact that makes a rollback a
    # mitigation rather than a fix: whatever brought the bad revision in will
    # bring it back the moment it is allowed to.
    a_staged_cache_misconfiguration(client)

    refused = client.post("/argocd/io-shop/rollback", json={"id": 1})

    assert refused.status_code == 400


def test_a_rollback_to_a_revision_never_deployed_is_refused(
    client: TestClient
) -> None:
    a_staged_cache_misconfiguration(client)
    suspend_automated_sync(client)

    refused = client.post("/argocd/io-shop/rollback", json={"id": 99})

    assert refused.status_code == 400


def test_suspending_automated_sync_then_rolling_back_is_accepted(
    client: TestClient
) -> None:
    a_staged_cache_misconfiguration(client)
    suspend_automated_sync(client)

    rolled_back = client.post("/argocd/io-shop/rollback", json={"id": 1})

    assert rolled_back.status_code == 200


def test_the_cache_scenario_reports_a_hit_ratio_and_a_flat_error_rate(
    client: TestClient
) -> None:
    a_staged_cache_misconfiguration(client)

    newest = the_newest_minute(client)

    assert newest["cache_hit_ratio"] == 0.0
    assert newest["error_rate"] < 0.05


# What tells the rollout's minutes from the shop's own, and how far the other
# two quantiles are allowed to drift across that split. The same figures the
# e2e case uses, for the same reason: the tail sits near 200ms while nothing is
# rolled out and well over a second while it is, so doubling is a partition and
# not a threshold.
THE_TAIL_AT_LEAST_DOUBLES = 2.0
THE_AGGREGATES_GROW_BY_NO_MORE_THAN = 1.25
A_CALM_ERROR_RATE = 0.05


@pytest.fixture
def a_shop_whose_flag_is_on() -> Iterator[TestClient]:
    """The service with its feature flag reading on, for one test.

    The `client` fixture's provider answers off, which is the *healthy* state
    for a scenario staged through the feature flag - so a rollout seeded against
    it reconciles to recovered on the first read and the window holds no slow
    minutes to compare. This one answers where seeding left it.
    """
    flags = Mock(spec=FlagClient)
    flags.is_enabled.return_value = True
    flags.name = "monthly-spend-feature"
    fallback_flags = Mock(spec=FlagClient)
    fallback_flags.is_enabled.return_value = True
    fallback_flags.name = "legacy-checkout-fallback"

    was = app_module.state
    # Both of the provider's history seams stubbed, for one reason: each reaches
    # the provider's own database, which no test here has. One clears the log on a
    # reset; the other backdates a staging toggle to when the change it stages
    # actually happened.
    app_module.state = ScenarioState(flags, fallback_flags, Mock(), Mock())
    was_read_directly = _the_catalogs_own_clients_replaced_by(flags, fallback_flags)
    forget_every_visit()

    yield TestClient(app)

    app_module.state = was
    _the_catalogs_own_clients_replaced_by(*was_read_directly)
    forget_every_visit()


def a_staged_slow_rollout(client: TestClient) -> None:
    seeded = client.post("/scenario/seed", json={"scenario_id": SLOW_CANARY_ROLLOUT})

    assert seeded.status_code == 200


def the_shops_window(client: TestClient) -> list[dict]:
    buckets = client.get("/scenario/metrics").json()

    assert buckets

    return buckets


def the_middle_of(figures: Iterable[float]) -> float:
    """The median of a window's readings, without the import.

    A plain sort rather than `statistics.median`: the window is small, and what
    an assertion needs from the middle of it is a figure the shop actually
    reported rather than an average of two.
    """
    ordered = sorted(figures)

    assert ordered

    return float(ordered[len(ordered) // 2])


def test_the_rollout_scenario_is_seedable_by_id(
    a_shop_whose_flag_is_on: TestClient
) -> None:
    a_staged_slow_rollout(a_shop_whose_flag_is_on)

    status = a_shop_whose_flag_is_on.get("/scenario/status").json()

    assert status["active_scenario"] == SLOW_CANARY_ROLLOUT


def test_the_rollout_moves_only_the_tail_as_the_service_serves_it(
    a_shop_whose_flag_is_on: TestClient
) -> None:
    # The claim this scenario exists for, asserted where the *arrangement* is
    # made rather than where the condition is. `test_generator.py` proves a
    # rollout handed to the generator moves one series; this proves `app.py`
    # hands it the arrangement that condition belongs to, deriving the rollout
    # from the flag's own timeline. That half is what was wrong when the
    # scenario shipped two faults instead of one, and only a 30-minute e2e run
    # was checking it.
    a_staged_slow_rollout(a_shop_whose_flag_is_on)

    window = the_shops_window(a_shop_whose_flag_is_on)
    quietest = min(minute["p99_ms"] for minute in window)
    a_moved_tail = quietest * THE_TAIL_AT_LEAST_DOUBLES
    slow = [minute for minute in window if minute["p99_ms"] > a_moved_tail]
    ordinary = [minute for minute in window if minute["p99_ms"] <= a_moved_tail]

    # Both halves, so a window where nothing was ever staged fails here rather
    # than passing on an empty comparison.
    assert slow
    assert ordinary

    tail_before = the_middle_of(minute["p99_ms"] for minute in ordinary)
    tail_after = the_middle_of(minute["p99_ms"] for minute in slow)
    median_before = the_middle_of(minute["p50_ms"] for minute in ordinary)
    median_after = the_middle_of(minute["p50_ms"] for minute in slow)
    p95_before = the_middle_of(minute["p95_ms"] for minute in ordinary)
    p95_after = the_middle_of(minute["p95_ms"] for minute in slow)

    assert tail_after >= tail_before * THE_TAIL_AT_LEAST_DOUBLES
    assert median_after <= median_before * THE_AGGREGATES_GROW_BY_NO_MORE_THAN
    assert p95_after <= p95_before * THE_AGGREGATES_GROW_BY_NO_MORE_THAN


def test_the_rollout_fails_no_request_as_the_service_serves_it(
    a_shop_whose_flag_is_on: TestClient
) -> None:
    # The regression for the two faults. The flag this scenario stages is the
    # same one the `ZeroDivisionError` canary sits behind, so an arrangement
    # shipping the monthly summary alongside the rollout puts the error rate at
    # several times its idle - and this is the one incident in which nothing
    # fails. Read across the whole window, not its newest minute: a rate that
    # moved and came back is still a second fault.
    a_staged_slow_rollout(a_shop_whose_flag_is_on)

    window = the_shops_window(a_shop_whose_flag_is_on)

    assert max(minute["error_rate"] for minute in window) < A_CALM_ERROR_RATE


def test_the_statement_scenario_is_seedable_by_id(client: TestClient) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": MONTHLY_STATEMENT_PANEL}
    )

    assert seeded.status_code == 200
    assert client.get("/scenario/status").json()["active_scenario"] == (
        MONTHLY_STATEMENT_PANEL
    )


def test_the_statement_scenario_is_not_offered_to_an_audience(
    client: TestClient
) -> None:
    # It stages the monthly-summary incident with the fault in a different
    # file, which is a distinction nothing an audience can see - so offering it
    # beside the original would be offering the same demo twice.
    catalogue = client.get("/scenario/catalog").json()["scenarios"]

    assert MONTHLY_STATEMENT_PANEL not in [scenario["id"] for scenario in catalogue]


def a_staged_slow_dependency(client: TestClient) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": PRICING_SERVICE_DEGRADED}
    )

    assert seeded.status_code == 200


def the_pod_of(client: TestClient, application: str) -> str | None:
    nodes = client.get(f"/argocd/{application}/resource-tree").json()["nodes"]

    return nodes[0]["createdAt"] if nodes else None


def test_the_registry_answers_without_anything_staged(client: TestClient) -> None:
    # The coupling was there all along, and that nobody had looked is the
    # incident. A registry that only answered during one would be a fixture
    # telling the reader where to look.
    listed = client.get("/registry/services/io-shop").json()

    assert listed["service"] == "io-shop"
    assert {entry["name"] for entry in listed["dependencies"]} >= {
        "io-pricing", "io-pay"
    }


def test_the_registry_says_which_dependency_is_this_companys(
    client: TestClient
) -> None:
    whose = {
        entry["name"]: entry["ownership"]
        for entry in client.get("/registry/services/io-shop").json()["dependencies"]
    }

    assert whose["io-pricing"] == "internal"
    assert whose["io-pay"] == "third-party"


def test_each_application_reports_its_own_pod(client: TestClient) -> None:
    a_staged_slow_dependency(client)

    assert the_pod_of(client, "io-shop") is not None
    assert the_pod_of(client, "io-pricing") is not None


def test_nothing_staged_means_no_pods_to_report(client: TestClient) -> None:
    # A creation time invented here would let a restart be confirmed against a
    # world that does not exist.
    assert the_pod_of(client, "io-shop") is None


def test_restarting_the_shop_leaves_the_pricing_services_pod_alone(
    client: TestClient
) -> None:
    a_staged_slow_dependency(client)
    was = the_pod_of(client, "io-pricing")

    restarted = client.post(
        "/argocd/io-shop/resource/actions/v2", json={"action": RESTART_ACTION}
    )

    assert restarted.status_code == 200
    assert the_pod_of(client, "io-pricing") == was


def test_restarting_the_pricing_service_leaves_the_shops_pod_alone(
    client: TestClient
) -> None:
    # The two halves of the same claim, and the reason the address on the action
    # is load-bearing rather than decorative: a stand-in that restarted the shop
    # whoever was named would grade every mitigation as correct.
    a_staged_slow_dependency(client)
    was = the_pod_of(client, "io-shop")

    restarted = client.post(
        "/argocd/io-pricing/resource/actions/v2", json={"action": RESTART_ACTION}
    )

    assert restarted.status_code == 200
    assert the_pod_of(client, "io-shop") == was
    assert the_pod_of(client, "io-pricing") != was


def test_the_slow_dependency_scenario_is_seedable_by_id(client: TestClient) -> None:
    a_staged_slow_dependency(client)

    assert client.get("/scenario/status").json()["active_scenario"] == (
        PRICING_SERVICE_DEGRADED
    )


def test_a_slow_dependency_moves_every_quantile_and_no_error_rate(
    client: TestClient
) -> None:
    a_staged_slow_dependency(client)

    minute = the_newest_minute(client)

    assert minute["p50_ms"] > 1000
    assert minute["p95_ms"] > 1000
    assert minute["p99_ms"] > 1000
    assert minute["error_rate"] < 0.05


def test_a_slow_dependency_leaves_the_deploy_history_empty(
    client: TestClient
) -> None:
    # The shape says "a deployment" and there is no deployment. That is what
    # sends a reader to the logs, which is where the answer is.
    a_staged_slow_dependency(client)

    history = client.get("/argocd/io-shop").json()["status"]["history"]

    assert history == []


def test_a_slow_dependency_says_in_the_logs_where_the_time_went(
    client: TestClient
) -> None:
    a_staged_slow_dependency(client)

    lines = client.get("/logs").json()
    named = [line for line in lines if "pricing.io-internal.svc" in line]

    assert named
    assert all("WARN" in line for line in named)


def the_replicas_of(client: TestClient, application: str) -> int:
    """How many replicas the platform says that application is running.

    Through the manifest, because that is how the endpoint answers: a string the
    caller parses, which is Argo CD's own shape and therefore the work a real
    adapter has to do.
    """
    resource = client.get(f"/argocd/{application}/resource")

    assert resource.status_code == 200

    return int(json.loads(resource.json()["manifest"])["spec"]["replicas"])


def scaled(client: TestClient, application: str, replicas: str) -> Response:
    return client.post(
        f"/argocd/{application}/resource/actions/v2",
        json={
            "action": SCALE_ACTION,
            "resourceActionParameters": [
                {"name": REPLICAS_PARAMETER, "value": replicas}
            ],
        },
    )


def test_the_deployment_starts_at_the_count_its_values_file_asks_for(
    client: TestClient
) -> None:
    assert the_replicas_of(client, "io-shop") == the_deployed_replica_count()


def test_scaling_through_the_platform_changes_what_is_running(
    client: TestClient
) -> None:
    assert scaled(client, "io-shop", "6").status_code == 200
    assert the_replicas_of(client, "io-shop") == 6


def test_what_is_running_stops_agreeing_with_the_repository(
    client: TestClient
) -> None:
    # The whole reason the count is read from the platform rather than the values
    # file: after one scale-out they are different numbers, and a caller
    # recording the count it replaced needs the one in force.
    scaled(client, "io-shop", "6")

    assert the_deployed_replica_count() == 3
    assert the_replicas_of(client, "io-shop") == 6


def test_a_scale_with_no_count_is_refused(client: TestClient) -> None:
    refused = client.post(
        "/argocd/io-shop/resource/actions/v2", json={"action": SCALE_ACTION}
    )

    assert refused.status_code == 400
    assert the_replicas_of(client, "io-shop") == the_deployed_replica_count()


def test_a_count_that_is_not_a_number_is_refused(client: TestClient) -> None:
    # Argo CD's own action errors on this, and the server reports a failed
    # action - which is what a caller has to be able to tell from a size it set.
    assert scaled(client, "io-shop", "not_a_number").status_code == 400
    assert the_replicas_of(client, "io-shop") == the_deployed_replica_count()


def test_a_count_below_one_is_refused(client: TestClient) -> None:
    assert scaled(client, "io-shop", "0").status_code == 400
    assert the_replicas_of(client, "io-shop") == the_deployed_replica_count()


def test_scaling_the_pricing_service_is_refused(client: TestClient) -> None:
    # The fixture holds no size for it, and a 200 would have a caller believe a
    # neighbour grew.
    assert scaled(client, "io-pricing", "6").status_code == 400
    assert the_replicas_of(client, "io-shop") == the_deployed_replica_count()


def test_the_pricing_service_reports_no_managed_deployment(
    client: TestClient
) -> None:
    assert client.get("/argocd/io-pricing/resource").status_code == 400


def test_a_reset_returns_the_deployment_to_the_size_it_is_configured_for(
    client: TestClient
) -> None:
    # A run abandoned between a scale-out and its withdrawal is exactly how the
    # next scenario gets staged onto capacity the deployment never asked for.
    scaled(client, "io-shop", "6")

    client.post("/scenario/reset")

    assert the_replicas_of(client, "io-shop") == the_deployed_replica_count()


def test_a_settled_shop_came_up_before_the_window_it_is_read_in() -> None:
    # These two were level once: the uptime was six hours while the window was
    # ninety minutes, and widening the window to six hours put a settled shop's
    # start time exactly on the window's earliest minute. That is the one
    # position which reads as "it came up just before all this", which is the
    # single thing this field exists to report - so a shop nobody restarted
    # would have been reporting a restart.
    assert SETTLED_UPTIME > timedelta(minutes=GENERATED_SPAN_MINUTES)


def a_staged_surge(client: TestClient) -> None:
    seeded = client.post("/scenario/seed", json={"scenario_id": CPU_SATURATION})

    assert seeded.status_code == 200


def test_the_surge_scenario_is_seedable_by_id(client: TestClient) -> None:
    a_staged_surge(client)

    assert client.get("/scenario/status").json()["active_scenario"] == CPU_SATURATION


def test_a_surge_moves_every_quantile_and_no_error_rate(client: TestClient) -> None:
    a_staged_surge(client)

    minute = the_newest_minute(client)

    assert minute["p50_ms"] > 200
    assert minute["p95_ms"] > 1000
    assert minute["p99_ms"] > 1000
    assert minute["error_rate"] < 0.05


def test_a_surge_reports_its_traffic_and_pins_its_cpu(client: TestClient) -> None:
    a_staged_surge(client)

    minute = the_newest_minute(client)

    assert minute["request_volume"] > 5000
    assert minute["cpu_used_cores"] == minute["cpu_limit_cores"]


def test_a_surge_leaves_the_heap_where_it_was(client: TestClient) -> None:
    # A climbing heap is the leak's signal entirely. A scenario that moved both
    # would leave a reader unable to say which resource ran out.
    a_staged_surge(client)

    assert the_newest_minute(client)["memory_used_bytes"] < BASELINE_MEMORY_BYTES * 1.1


def test_a_surge_leaves_the_deploy_history_empty(client: TestClient) -> None:
    # The shape says "a deployment" and nothing was deployed, which is what makes
    # the volume and the utilisation the only evidence naming a cause.
    a_staged_surge(client)

    assert client.get("/argocd/io-shop").json()["status"]["history"] == []


def test_scaling_the_shop_out_ends_the_surge(client: TestClient) -> None:
    a_staged_surge(client)
    saturated = the_newest_minute(client)

    assert scaled(client, "io-shop", "6").status_code == 200

    after = the_newest_minute(client)

    assert after["cpu_limit_cores"] == 6.0
    assert after["cpu_used_cores"] < after["cpu_limit_cores"]
    assert after["p50_ms"] < saturated["p50_ms"] / 3


def test_restarting_the_shop_does_not_end_a_surge(client: TestClient) -> None:
    # The wrong answer, and the reason the split between the two halves of
    # resource exhaustion is worth having: demand and capacity are both where they
    # were, so the shop is saturated again from its first served minute.
    a_staged_surge(client)
    saturated = the_newest_minute(client)

    restarted = client.post(RESTART_ACTION_PATH, json={"action": RESTART_ACTION})

    assert restarted.status_code == 200

    after = the_newest_minute(client)

    assert after["process_start_time_seconds"] != (
        saturated["process_start_time_seconds"]
    )
    assert after["cpu_used_cores"] == after["cpu_limit_cores"]
    assert after["p50_ms"] > saturated["p50_ms"] / 2


def test_putting_the_count_back_returns_the_shop_to_saturation(
    client: TestClient
) -> None:
    a_staged_surge(client)
    scaled(client, "io-shop", "6")
    relieved = the_newest_minute(client)

    scaled(client, "io-shop", "3")

    after = the_newest_minute(client)

    assert after["cpu_used_cores"] == after["cpu_limit_cores"] == 3.0
    assert after["p50_ms"] > relieved["p50_ms"] * 3


AN_AUTOSCALER = {
    "kind": AUTOSCALER_KIND,
    "group": AUTOSCALER_GROUP,
    "version": AUTOSCALER_VERSION,
}


def a_staged_flapping_autoscaler(client: TestClient) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": AUTOSCALER_FLAPPING}
    )

    assert seeded.status_code == 200


def the_autoscaler_of(client: TestClient, application: str) -> dict:
    """The autoscaler's spec, as the platform hands it over.

    Through the manifest for the reason the Deployment's count is read through
    one: a string the caller parses is Argo CD's own shape, and therefore the work
    a real adapter has to do.
    """
    resource = client.get(f"/argocd/{application}/resource", params=AN_AUTOSCALER)

    assert resource.status_code == 200

    spec: dict = json.loads(resource.json()["manifest"])["spec"]

    return spec


def patched(client: TestClient,
            application: str,
            patch: str,
            patch_type: str = MERGE_PATCH_TYPE,
            kind: str = AUTOSCALER_KIND) -> Response:
    return client.post(
        f"/argocd/{application}/resource",
        params={**AN_AUTOSCALER, "kind": kind, "patchType": patch_type},
        content=patch,
    )


def a_floor_of(replicas: int) -> str:
    return json.dumps({SPEC_FIELD: {MIN_REPLICAS_FIELD: replicas}})


def test_no_autoscaler_is_reported_until_one_is_staged(client: TestClient) -> None:
    # A platform with no autoscaler deployed has none to report, and a fixture
    # inventing bounds would let a pin be confirmed against a controller that
    # does not exist.
    absent = client.get("/argocd/io-shop/resource", params=AN_AUTOSCALER)

    assert absent.status_code == 404


def test_a_staged_autoscaler_reports_the_bounds_it_is_declared_with(
    client: TestClient
) -> None:
    a_staged_flapping_autoscaler(client)

    spec = the_autoscaler_of(client, "io-shop")

    assert spec[MIN_REPLICAS_FIELD] == 3
    assert spec["maxReplicas"] == 6


def test_the_manifest_carries_the_window_that_makes_it_flap(
    client: TestClient
) -> None:
    # The whole resource rather than the two fields a mitigation writes: a reader
    # sent to find out whether the autoscaler is at fault can see the zero window
    # that makes it one, instead of taking the diagnosis on trust.
    a_staged_flapping_autoscaler(client)

    spec = the_autoscaler_of(client, "io-shop")

    assert spec["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 0


def test_the_deployment_still_answers_beside_the_autoscaler(
    client: TestClient
) -> None:
    # One route, two kinds. The count is on one resource and the bounds that
    # decide the count are on the other, and `kind` is what chooses.
    a_staged_flapping_autoscaler(client)

    assert the_replicas_of(client, "io-shop") == the_deployed_replica_count()


def test_patching_the_floor_raises_it(client: TestClient) -> None:
    a_staged_flapping_autoscaler(client)

    assert patched(client, "io-shop", a_floor_of(6)).status_code == 200
    assert the_autoscaler_of(client, "io-shop")[MIN_REPLICAS_FIELD] == 6


def test_patching_the_floor_back_lowers_it_again(client: TestClient) -> None:
    # Both directions, because the floor is what a withdrawal puts back.
    a_staged_flapping_autoscaler(client)
    patched(client, "io-shop", a_floor_of(6))

    assert patched(client, "io-shop", a_floor_of(3)).status_code == 200
    assert the_autoscaler_of(client, "io-shop")[MIN_REPLICAS_FIELD] == 3


def test_a_patch_of_anything_but_the_autoscaler_is_refused(
    client: TestClient
) -> None:
    # A platform answering 200 to a patch it did not apply would have a caller
    # believe production had changed when it had not.
    a_staged_flapping_autoscaler(client)

    refused = patched(client, "io-shop", a_floor_of(6), kind="Deployment")

    assert refused.status_code == 400
    assert the_autoscaler_of(client, "io-shop")[MIN_REPLICAS_FIELD] == 3


def test_a_patch_type_the_stand_in_does_not_apply_is_refused(
    client: TestClient
) -> None:
    # A JSON patch is a list of operations rather than a document, so reading one
    # shape and claiming the other would teach a caller a wire that does not
    # exist.
    a_staged_flapping_autoscaler(client)

    refused = patched(
        client, "io-shop", a_floor_of(6), patch_type="application/json-patch+json"
    )

    assert refused.status_code == 400


def test_a_patch_that_does_not_reach_the_floor_is_refused(
    client: TestClient
) -> None:
    a_staged_flapping_autoscaler(client)

    assert patched(client, "io-shop", json.dumps({SPEC_FIELD: {}})).status_code == 400
    assert patched(client, "io-shop", "not a document").status_code == 400


def test_a_floor_below_one_is_refused(client: TestClient) -> None:
    a_staged_flapping_autoscaler(client)

    assert patched(client, "io-shop", a_floor_of(0)).status_code == 400
    assert the_autoscaler_of(client, "io-shop")[MIN_REPLICAS_FIELD] == 3


def test_patching_an_autoscaler_nothing_staged_is_refused(
    client: TestClient
) -> None:
    assert patched(client, "io-shop", a_floor_of(6)).status_code == 404


def test_a_reset_returns_the_autoscaler_to_the_floor_it_is_declared_with(
    client: TestClient
) -> None:
    # A run abandoned between a pin and its withdrawal is how the next flapping
    # scenario gets staged onto a controller that cannot scale down - an incident
    # that never starts.
    a_staged_flapping_autoscaler(client)
    patched(client, "io-shop", a_floor_of(6))

    client.post("/scenario/reset")
    a_staged_flapping_autoscaler(client)

    assert the_autoscaler_of(client, "io-shop")[MIN_REPLICAS_FIELD] == 3


def the_tree_of(client: TestClient, application: str) -> list[dict]:
    tree = client.get(f"/argocd/{application}/resource-tree")

    assert tree.status_code == 200

    nodes: list[dict] = tree.json()["nodes"]

    return nodes


def test_the_resource_tree_lists_the_autoscaler_when_one_is_staged(
    client: TestClient
) -> None:
    # How a pin finds the resource it is about to patch. A tree that listed only
    # the pod would have the mitigation refuse against a controller the platform
    # can describe perfectly well through its own manifest route.
    a_staged_flapping_autoscaler(client)

    autoscalers = [
        node for node in the_tree_of(client, "io-shop")
        if node["kind"] == AUTOSCALER_KIND
    ]

    assert len(autoscalers) == 1
    assert autoscalers[0]["group"] == AUTOSCALER_GROUP
    assert autoscalers[0]["version"] == AUTOSCALER_VERSION
    assert autoscalers[0]["namespace"] == "production"


def test_the_autoscaler_is_not_named_after_the_application(
    client: TestClient
) -> None:
    # Kubernetes does not require an autoscaler to share its target's name, so a
    # fixture that made them equal would let a caller send the application's name
    # as `resourceName` and pass - and the caller that addressed the resource
    # correctly would look no different.
    a_staged_flapping_autoscaler(client)

    named = [
        node["name"] for node in the_tree_of(client, "io-shop")
        if node["kind"] == AUTOSCALER_KIND
    ]

    assert named == ["io-shop-cpu"]
    assert "io-shop" not in named


def test_no_autoscaler_is_listed_for_a_scenario_that_stages_none(
    client: TestClient
) -> None:
    # The same condition the manifest route answers a 404 on, so the two channels
    # cannot disagree about whether a controller exists.
    seeded = client.post("/scenario/seed", json={"scenario_id": CPU_SATURATION})

    assert seeded.status_code == 200
    assert [
        node for node in the_tree_of(client, "io-shop")
        if node["kind"] == AUTOSCALER_KIND
    ] == []


def test_the_pod_is_still_listed_beside_the_autoscaler(
    client: TestClient
) -> None:
    # The pod's creation time is what confirms a restart landed, and a pin
    # arriving must not cost that.
    a_staged_flapping_autoscaler(client)

    kinds = [node["kind"] for node in the_tree_of(client, "io-shop")]

    assert "Pod" in kinds


def a_staged_paused_rollout(client: TestClient) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": HALF_FINISHED_ROLLOUT}
    )

    assert seeded.status_code == 200


def the_deployment_of(client: TestClient, application: str) -> dict:
    return json.loads(
        client.get(f"/argocd/{application}/resource").json()["manifest"]
    )


def test_a_converged_deployment_says_so_plainly(client: TestClient) -> None:
    # Twelve scenarios in thirteen, and the answer is evidence. A channel that
    # said nothing about a finished deployment is one a reader consults only
    # when they already suspect what it will say.
    a_staged_cache_misconfiguration(client)

    deployment = the_deployment_of(client, "io-shop")

    assert deployment["spec"]["paused"] is False
    assert deployment["status"]["replicas"] == the_deployed_replica_count()
    assert deployment["status"]["updatedReplicas"] == the_deployed_replica_count()


def test_a_paused_rollout_reports_replicas_on_two_revisions(
    client: TestClient
) -> None:
    # The fact the deploy history cannot hold: it says a sync happened and says
    # nothing about whether the pods finished turning over.
    a_staged_paused_rollout(client)

    deployment = the_deployment_of(client, "io-shop")

    assert deployment["spec"]["paused"] is True
    assert deployment["status"]["replicas"] == 6
    assert deployment["status"]["updatedReplicas"] == 3


def test_a_paused_rollout_leaves_the_size_the_repository_asks_for(
    client: TestClient
) -> None:
    # A surge is the platform's business for a few minutes, not a capacity
    # anybody deployed - so `spec.replicas` is still what a caller deriving a
    # new count reads, and the shop still reports three replicas' worth of CPU.
    a_staged_paused_rollout(client)

    deployment = the_deployment_of(client, "io-shop")

    assert deployment["spec"]["replicas"] == the_deployed_replica_count()
    assert the_newest_minute(client)["cpu_limit_cores"] == float(
        the_deployed_replica_count()
    )


def test_a_paused_rollout_says_since_when_it_has_been_holding(
    client: TestClient
) -> None:
    a_staged_paused_rollout(client)

    condition = the_deployment_of(client, "io-shop")["status"]["conditions"][0]

    assert condition["reason"] == "DeploymentPaused"
    assert condition["status"] == "Unknown"
    assert condition["lastTransitionTime"] < to_bucket_id(datetime.now(UTC))


def test_rolling_the_deployment_back_converges_the_manifest(
    client: TestClient
) -> None:
    a_staged_paused_rollout(client)
    suspend_automated_sync(client)

    rolled_back = client.post("/argocd/io-shop/rollback", json={"id": 1})

    assert rolled_back.status_code == 200

    deployment = the_deployment_of(client, "io-shop")

    assert deployment["spec"]["paused"] is False
    assert deployment["status"]["replicas"] == the_deployed_replica_count()
    assert deployment["status"]["conditions"][0]["reason"] == (
        "NewReplicaSetAvailable"
    )


def test_the_paused_rollout_reports_one_deploy_at_the_onset(
    client: TestClient
) -> None:
    # Two entries, as every generated scenario staging a change has: the one
    # being rolled out, and the revision before it that a rollback is addressed
    # to. Only one of them is at the onset.
    a_staged_paused_rollout(client)

    history = client.get("/argocd/io-shop").json()["status"]["history"]

    assert len(history) == 2
    assert history[-1]["revision"] != history[0]["revision"]


def a_typical_quiet_minute(buckets: list[dict]) -> dict[str, float]:
    """The middle of the window's quiet minutes, quantile by quantile.

    A single minute will not do as the baseline here, and the reason is the cache
    rather than anything about this scenario. The quantiles of a cached shop are
    taken over what it actually served, so a minute that happened to draw ten
    misses instead of twenty reports a 95th percentile down in the cached band -
    about 37ms against the usual 190. Measured over two thousand minutes, one in a
    hundred does exactly that.

    Read off one arbitrary bucket, that is a baseline a hundred times too low and
    a test that fails for the whole minute it is in. The middle of three hundred
    minutes cannot be an unlucky draw.
    """
    quiet = buckets[:300]

    return {
        quantile: median(bucket[quantile] for bucket in quiet)
        for quantile in ("p50_ms", "p95_ms", "p99_ms")
    }


def test_the_paused_rollout_moves_the_error_rate_and_not_the_quantiles(
    client: TestClient
) -> None:
    a_staged_paused_rollout(client)

    buckets = client.get("/scenario/metrics").json()
    split = buckets[-2]
    quiet = a_typical_quiet_minute(buckets)

    assert split["error_rate"] > 0.12
    assert split["p50_ms"] < quiet["p50_ms"] * 1.5
    assert split["p95_ms"] < quiet["p95_ms"] * 1.5
    assert split["p99_ms"] < quiet["p99_ms"] * 1.5


def test_the_paused_rollout_keeps_the_cache_answering(client: TestClient) -> None:
    # What tells this apart from the shop losing its cache altogether.
    a_staged_paused_rollout(client)

    assert the_newest_minute(client)["cache_hit_ratio"] > 0.8


def test_the_paused_rollout_pages_about_an_error_rate(client: TestClient) -> None:
    # The only judged series this incident moves. Without a rule nothing pages
    # and no walk starts.
    a_staged_paused_rollout(client)

    fired = an_alert_for(HALF_FINISHED_ROLLOUT, datetime.now(UTC))

    assert fired["alerts"][0]["labels"]["alertname"] == "HighErrorRate"


def test_the_paused_rollout_is_offered_in_the_console(client: TestClient) -> None:
    # The split fleet on the page is the story, so unlike the statement panel
    # this one is not hidden.
    catalog = client.get("/scenario/catalog").json()

    offered = {scenario["id"] for scenario in catalog["scenarios"]}
    families = {family["id"] for family in catalog["families"]}

    assert HALF_FINISHED_ROLLOUT in offered
    assert "half-rolled-out" in families


def test_withdrawing_the_rollback_returns_the_shop_to_the_mixture(
    client: TestClient
) -> None:
    # The newest history entry is the revision the application was already on,
    # so asking for it is the withdrawal rather than a second rollback - and the
    # platform reports a fleet split again.
    a_staged_paused_rollout(client)
    suspend_automated_sync(client)
    client.post("/argocd/io-shop/rollback", json={"id": 1})

    withdrawn = client.post("/argocd/io-shop/rollback", json={"id": 2})

    assert withdrawn.status_code == 200

    deployment = the_deployment_of(client, "io-shop")

    assert deployment["spec"]["paused"] is True
    assert deployment["status"]["replicas"] == 6
    assert deployment["status"]["updatedReplicas"] == 3


def test_a_converged_fleet_says_when_it_converged(client: TestClient) -> None:
    # The instant a reader wants: how long the fleet has been on one revision.
    # Dating it from the process's start would put a convergence that happened a
    # minute ago twelve hours back.
    a_staged_paused_rollout(client)
    suspend_automated_sync(client)
    client.post("/argocd/io-shop/rollback", json={"id": 1})

    condition = the_deployment_of(client, "io-shop")["status"]["conditions"][0]

    assert condition["lastTransitionTime"] == to_bucket_id(datetime.now(UTC))


def a_staged_drift(client: TestClient) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": SILENT_DATA_CORRUPTION}
    )

    assert seeded.status_code == 200


def test_the_drifting_totals_scenario_is_seedable_by_id(client: TestClient) -> None:
    a_staged_drift(client)

    assert client.get("/scenario/status").json()["active_scenario"] == (
        SILENT_DATA_CORRUPTION
    )


def test_the_console_offers_it_under_a_foundational_integrity_family(
    client: TestClient
) -> None:
    # A family the rail did not have. Every family above it is covered entire, and
    # this is the first one chosen by its share of real incidents rather than by
    # elimination - so the group has to exist for the scenario to be offered at
    # all.
    catalog = client.get("/scenario/catalog").json()

    families = {family["id"]: family for family in catalog["families"]}
    offered = {
        scenario["id"]: scenario["family"] for scenario in catalog["scenarios"]
    }

    assert offered[SILENT_DATA_CORRUPTION] == "foundational-integrity"
    assert "12%" in families["foundational-integrity"]["taxonomy"]


def test_the_family_sits_where_its_share_of_incidents_puts_it(
    client: TestClient
) -> None:
    # The rail is the one place the shop says what kinds of incident exist at all,
    # and it is ordered by share, largest first. Twelve percent goes below capacity
    # at thirteen and above the tail at three.
    order = [
        family["id"] for family in client.get("/scenario/catalog").json()["families"]
    ]

    assert order.index("capacity") < order.index("foundational-integrity")
    assert order.index("foundational-integrity") < order.index("the-tail")


def test_the_drifting_shop_is_paged_about_by_its_integrity_check(
    client: TestClient
) -> None:
    # The whole channel, and the wiring between the two halves of it. No series
    # moved, so no rule on a series fired; what found this is the shop's own job,
    # and what the monitoring stack has to page about is the finding rather than a
    # window somebody could go and read.
    a_staged_drift(client)

    fired = an_alert_for(
        SILENT_DATA_CORRUPTION,
        datetime.now(UTC),
        app_module.state.the_integrity_check_found(),
    )["alerts"][0]

    assert fired["labels"]["alertname"] == "SpendTotalsDoNotReconcile"
    assert "onset" in fired["annotations"]


def test_the_onset_it_is_paged_with_is_a_week_older_than_the_page(
    client: TestClient
) -> None:
    # The reason the onset is in the payload at all. The check runs weekly, so when
    # it fired says nothing about when the writing went wrong - and a consumer that
    # took `startsAt` for the onset would anchor the incident on the wrong week
    # entirely.
    a_staged_drift(client)

    fired = an_alert_for(
        SILENT_DATA_CORRUPTION,
        datetime.now(UTC),
        app_module.state.the_integrity_check_found(),
    )["alerts"][0]

    began = datetime.strptime(
        fired["annotations"]["onset"], TIMESTAMP_FORMAT
    ).replace(tzinfo=UTC)

    assert datetime.now(UTC) - began > timedelta(days=6)


def test_a_shop_with_nothing_wrong_with_its_totals_is_paged_about_its_own_fault(
    client: TestClient
) -> None:
    # The job is the shop's and runs whatever is going on, so every alert is
    # offered its finding. On a shop whose totals are in order it has to decide
    # nothing, and the scenario's own rule is what fires.
    a_staged_leak(client)

    fired = an_alert_for(
        RESOURCE_LEAK,
        datetime.now(UTC),
        app_module.state.the_integrity_check_found(),
    )["alerts"][0]

    assert fired["labels"]["alertname"] == "HighMemoryUsage"
    assert "onset" not in fired["annotations"]


def a_staged_deployed_drift(client: TestClient) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": MONTHLY_TOTALS_FALLING_BEHIND}
    )

    assert seeded.status_code == 200


def the_onset_paged_with(client: TestClient) -> datetime:
    """The onset the integrity check's alert states, as an instant."""
    fired = an_alert_for(
        MONTHLY_TOTALS_FALLING_BEHIND,
        datetime.now(UTC),
        app_module.state.the_integrity_check_found(),
    )["alerts"][0]

    return datetime.strptime(
        fired["annotations"]["onset"], TIMESTAMP_FORMAT
    ).replace(tzinfo=UTC)


def test_the_console_offers_the_deployed_drift_beside_the_flag_one(
    client: TestClient
) -> None:
    offered = {
        scenario["id"]: scenario["family"]
        for scenario in client.get("/scenario/catalog").json()["scenarios"]
    }

    assert offered[MONTHLY_TOTALS_FALLING_BEHIND] == "foundational-integrity"


def test_the_console_offers_the_deliberate_rename_beside_the_blind_spot(
    client: TestClient
) -> None:
    offered = {
        scenario["id"]: scenario["family"]
        for scenario in client.get("/scenario/catalog").json()["scenarios"]
    }

    assert offered[MONITORING_CONFIGURATION_DRIFT] == "foundational-integrity"


def test_the_deploy_history_names_the_revision_that_applied_the_convention(
    client: TestClient
) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": MONITORING_CONFIGURATION_DRIFT}
    )
    assert seeded.status_code == 200

    history = client.get("/argocd/io-shop").json()["status"]["history"]

    assert history[-1]["revision"] == THE_COMMIT_THAT_NAMED_EVERY_PORT_FOR_ITS_PROTOCOL


def test_the_deliberate_rename_publishes_nothing_from_its_onset(
    client: TestClient
) -> None:
    # The window stops rather than empties: the quiet stretch before the
    # revision is there, and nothing at or after it.
    client.post(
        "/scenario/seed", json={"scenario_id": MONITORING_CONFIGURATION_DRIFT}
    )

    buckets = client.get("/scenario/metrics").json()
    went_quiet = app_module.state.the_minute_the_shop_went_quiet()

    assert buckets
    assert all(
        datetime.strptime(bucket["bucket_id"], TIMESTAMP_FORMAT).replace(tzinfo=UTC)
        < went_quiet
        for bucket in buckets
    )


def test_the_deploy_history_names_the_revision_that_stopped_carrying_the_month(
    client: TestClient
) -> None:
    a_staged_deployed_drift(client)

    history = client.get("/argocd/io-shop").json()["status"]["history"]

    assert history[-1]["revision"] == THE_COMMIT_THAT_STOPPED_CARRYING_THE_MONTH


def test_the_revision_landed_at_the_minute_the_alert_dates(
    client: TestClient
) -> None:
    # What makes the deploy the evidence: a consumer looking for what changed
    # at or just before the stated onset finds this entry and nothing else. The
    # first mis-recorded purchase is somebody's next order after the landing,
    # so the two are within a couple of minutes and in that order.
    a_staged_deployed_drift(client)

    landed = datetime.strptime(
        client.get("/argocd/io-shop").json()["status"]["history"][-1]["deployedAt"],
        TIMESTAMP_FORMAT
    ).replace(tzinfo=UTC)
    onset = the_onset_paged_with(client)

    assert landed <= onset < landed + timedelta(minutes=5)


def test_the_deployed_drift_is_paged_about_a_week_late(client: TestClient) -> None:
    a_staged_deployed_drift(client)

    assert datetime.now(UTC) - the_onset_paged_with(client) > timedelta(days=6)


def test_the_deployed_drift_fails_no_request(client: TestClient) -> None:
    # Nothing the generator is handed differs from a quiet shop's, which is the
    # point: the revision changes what is written down and nothing anybody
    # measures.
    a_staged_deployed_drift(client)

    buckets = client.get("/scenario/metrics").json()

    assert buckets
    assert all(bucket["error_rate"] < 0.05 for bucket in buckets)


def a_staged_unreachable_control_plane(client: TestClient) -> None:
    seeded = client.post(
        "/scenario/seed", json={"scenario_id": CONTROL_PLANE_UNREACHABLE}
    )

    assert seeded.status_code == 200


def test_the_unreachable_platform_scenario_is_seedable_by_id(
    client: TestClient
) -> None:
    a_staged_unreachable_control_plane(client)

    assert client.get("/scenario/status").json()["active_scenario"] == (
        CONTROL_PLANE_UNREACHABLE
    )


def test_a_rollback_is_refused_by_a_platform_that_will_not_act(
    client: TestClient
) -> None:
    # A 503 and not the 400 the same call earns from a platform that is
    # answering and reconciling itself. Ordering rather than pedantry: a caller
    # told its rollback was rejected would go and suspend sync, which is a
    # second call to a platform that is taking none.
    a_staged_unreachable_control_plane(client)

    refused = client.post("/argocd/io-shop/rollback", json={"id": 1})

    assert refused.status_code == 503


def test_a_restart_is_refused_by_a_platform_that_will_not_act(
    client: TestClient
) -> None:
    # The scale-out is the same route with a different action, so this covers
    # both of the two the resource-action endpoint carries.
    a_staged_unreachable_control_plane(client)

    refused = client.post(RESTART_ACTION_PATH, json={"action": RESTART_ACTION})

    assert refused.status_code == 503


def test_pinning_the_autoscaler_is_refused_by_a_platform_that_will_not_act(
    client: TestClient
) -> None:
    a_staged_unreachable_control_plane(client)

    refused = patched(client, "io-shop", a_floor_of(6))

    assert refused.status_code == 503


def test_suspending_sync_is_refused_by_a_platform_that_will_not_act(
    client: TestClient
) -> None:
    a_staged_unreachable_control_plane(client)

    refused = client.put("/argocd/io-shop/spec", json={"syncPolicy": {}})

    assert refused.status_code == 503


def test_the_platform_goes_on_reporting_what_it_has_deployed(
    client: TestClient
) -> None:
    a_staged_unreachable_control_plane(client)

    application = client.get("/argocd/io-shop")

    assert application.status_code == 200


def test_the_deployment_is_still_in_the_history_a_reader_diagnoses_from(
    client: TestClient
) -> None:
    # The whole reason the refusal is on the acting routes alone. A platform
    # that hid its own history would take the deployment out of the change
    # channel, no rollback would be ranked, and the incident would be about not
    # seeing rather than about not acting.
    a_staged_unreachable_control_plane(client)

    history = client.get("/argocd/io-shop").json()["status"]["history"]

    assert len(history) == 2


def test_the_resource_tree_still_answers(client: TestClient) -> None:
    a_staged_unreachable_control_plane(client)

    tree = client.get("/argocd/io-shop/resource-tree")

    assert tree.status_code == 200


def test_the_shop_goes_on_reporting_its_own_telemetry(
    client: TestClient
) -> None:
    # Load-bearing. A mitigation is judged by reading this, so a shop that went
    # down with its platform would leave every mitigation unjudgeable and the
    # incident would end for that reason instead of this one.
    a_staged_unreachable_control_plane(client)

    buckets = client.get("/scenario/metrics")

    assert buckets.status_code == 200
    assert buckets.json() != []


def test_resetting_returns_the_platform_to_acting(client: TestClient) -> None:
    a_staged_unreachable_control_plane(client)

    client.post("/scenario/reset")

    assert client.put(
        "/argocd/io-shop/spec", json={"syncPolicy": {}}
    ).status_code == 200


def test_the_console_offers_it_under_the_foundational_integrity_family(
    client: TestClient
) -> None:
    catalog = client.get("/scenario/catalog").json()

    offered = {
        scenario["id"]: scenario["family"] for scenario in catalog["scenarios"]
    }

    assert offered[CONTROL_PLANE_UNREACHABLE] == "foundational-integrity"


QUERY_RANGE_PATH = "/prometheus/api/v1/query_range"


def a_range_query(client: TestClient, query: str, start: str, end: str) -> Response:
    return client.get(
        QUERY_RANGE_PATH,
        params={"query": query, "start": start, "end": end, "step": "60s"}
    )


def a_minutes_end(bucket: dict) -> str:
    started = datetime.strptime(bucket["bucket_id"], TIMESTAMP_FORMAT).replace(tzinfo=UTC)

    return str((started + timedelta(minutes=1)).timestamp())


def test_the_prometheus_stand_in_answers_what_the_rows_say(client: TestClient) -> None:
    # The stand-in's whole licence: a consumer reading through it reads the
    # numbers the rows hold. Finished minutes only - the one in progress is
    # still moving between the two reads.
    a_staged_leak(client)
    rows = client.get("/scenario/metrics").json()[:-1]

    answered = a_range_query(
        client, QUERIES["memory_used_bytes"],
        start=a_minutes_end(rows[0]), end=a_minutes_end(rows[-1])
    )

    assert answered.status_code == 200
    assert answered.json()["status"] == "success"
    assert [
        value for _, value in answered.json()["data"]["result"][0]["values"]
    ] == [str(row["memory_used_bytes"]) for row in rows]


def test_the_prometheus_stand_in_refuses_a_query_it_does_not_know(
    client: TestClient
) -> None:
    refused = a_range_query(client, "up", start="0", end="60")

    assert refused.status_code == 400
    assert refused.json()["status"] == "error"
    assert refused.json()["errorType"] == "bad_data"


def test_the_prometheus_stand_in_refuses_a_request_missing_a_parameter(
    client: TestClient
) -> None:
    refused = client.get(QUERY_RANGE_PATH, params={"query": QUERIES["error_rate"]})

    assert refused.status_code == 400
    assert refused.json()["errorType"] == "bad_data"


def test_the_prometheus_stand_in_answers_nothing_staged_with_no_series(
    client: TestClient
) -> None:
    answered = a_range_query(
        client, QUERIES["error_rate"], start="0", end=str(time.time())
    )

    assert answered.status_code == 200
    assert answered.json()["data"]["result"] == []


def test_the_scrape_endpoint_is_prometheus_text(client: TestClient) -> None:
    a_staged_leak(client)

    scraped = client.get("/metrics")

    assert scraped.status_code == 200
    assert scraped.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# TYPE process_resident_memory_bytes gauge" in scraped.text
