from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from io_shop.visits import (
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
)
from target_app import app as app_module
from target_app.app import RESTART_ACTION, app
from target_app.flags import FlagClient
from target_app.generator import BASELINE_MEMORY_BYTES
from target_app.scenarios import RESOURCE_LEAK, UPSTREAM_DEPENDENCY_FAILURE
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


def test_the_leak_is_seedable_by_id(client: TestClient) -> None:
    a_staged_leak(client)

    assert client.get("/scenario/status").json()["active_scenario"] == RESOURCE_LEAK


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
    assert the_newest_minute(client)["memory_used_bytes"] < BASELINE_MEMORY_BYTES * 1.2


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
