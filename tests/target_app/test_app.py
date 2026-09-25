from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from io_shop.visits import (
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
)
from target_app import app as app_module
from target_app.app import GENERATED_SPAN_MINUTES, RESTART_ACTION, app
from target_app.flags import FlagClient
from target_app.generator import BASELINE_MEMORY_BYTES, SETTLED_UPTIME
from target_app.scenarios import (
    CACHE_MISCONFIGURED,
    MONTHLY_STATEMENT_PANEL,
    RESOURCE_LEAK,
    SLOW_CANARY_ROLLOUT,
    UPSTREAM_DEPENDENCY_FAILURE,
)
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
    app_module.state = ScenarioState(flags, fallback_flags, Mock())
    forget_every_visit()

    yield TestClient(app)

    app_module.state = was
    forget_every_visit()


def a_staged_leak(client: TestClient) -> None:
    seeded = client.post("/scenario/seed", json={"scenario_id": RESOURCE_LEAK})

    assert seeded.status_code == 200


def the_newest_minute(client: TestClient) -> dict:
    buckets = client.get("/metrics").json()

    assert buckets

    return buckets[-1]


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
        buckets = client.get("/metrics").json()
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


def test_the_upstream_failure_is_offered_in_the_console() -> None:
    # Worth watching: it is the scenario where the right answer is that Argus
    # does nothing, and an audience seeing that happen is the point of showing
    # it at all.
    offered = [
        entry["id"] for entry in TestClient(app).get("/scenario/catalog").json()["scenarios"]
    ]

    assert UPSTREAM_DEPENDENCY_FAILURE in offered


def test_the_upstream_failure_offers_no_flag_to_watch() -> None:
    # No flag is in play, so none may be badged. A page naming one would be
    # pointing an audience at a suspect the fixture invented, and offering a
    # control that changes nothing.
    catalog = TestClient(app).get("/scenario/catalog").json()
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
    assert client.get("/metrics").json() == []


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
    app_module.state = ScenarioState(flags, fallback_flags, Mock())
    forget_every_visit()

    yield TestClient(app)

    app_module.state = was
    forget_every_visit()


def a_staged_slow_rollout(client: TestClient) -> None:
    seeded = client.post("/scenario/seed", json={"scenario_id": SLOW_CANARY_ROLLOUT})

    assert seeded.status_code == 200


def the_shops_window(client: TestClient) -> list[dict]:
    buckets = client.get("/metrics").json()

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


def test_a_settled_shop_came_up_before_the_window_it_is_read_in() -> None:
    # These two were level once: the uptime was six hours while the window was
    # ninety minutes, and widening the window to six hours put a settled shop's
    # start time exactly on the window's earliest minute. That is the one
    # position which reads as "it came up just before all this", which is the
    # single thing this field exists to report - so a shop nobody restarted
    # would have been reporting a restart.
    assert SETTLED_UPTIME > timedelta(minutes=GENERATED_SPAN_MINUTES)
