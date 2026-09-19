from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import create_autospec

import httpx
import pytest
from target_app.monitoring import (
    AlertNotDelivered,
    MonitoringSettings,
    an_alert_for,
    fire_alert,
)
from target_app.scenarios import (
    BAD_DEPLOYMENT,
    FEATURE_FLAG_TOGGLE,
    RESOURCE_LEAK,
)

"""What the shop's monitoring promises about the alert it raises.

The webhook goes to something outside this repo, so every test here drives a
doubled transport. What matters is the payload's shape - a consumer parses it
with the code it points at a real Grafana - and that a delivery which did not
happen is never reported as one.
"""

SOME_INSTANT = datetime(2026, 8, 29, 10, 41, 0, tzinfo=UTC)
DONT_CARE_INSTANT = SOME_INSTANT


def a_settings(webhook_url: str = "http://argus.test/webhooks/alerts") -> MonitoringSettings:
    return MonitoringSettings(alert_webhook_url=webhook_url)


def an_accepting_receiver(incident_id: str = "dont-care-incident") -> object:
    post = create_autospec(httpx.post)
    post.return_value = httpx.Response(
        202,
        json={"incident_id": incident_id},
        request=httpx.Request("POST", "http://argus.test/webhooks/alerts"),
    )
    return post


def test_an_error_rate_scenario_fires_the_error_rate_rule() -> None:
    alert = an_alert_for(FEATURE_FLAG_TOGGLE, DONT_CARE_INSTANT)

    assert alert["alerts"][0]["labels"]["alertname"] == "HighErrorRate"


def test_a_deploy_that_slowed_the_shop_down_fires_the_latency_rule() -> None:
    # The bad deploy shows up as latency rather than as errors, and the alert
    # name is the only part of the payload that says which rule tripped.
    alert = an_alert_for(BAD_DEPLOYMENT, DONT_CARE_INSTANT)

    assert alert["alerts"][0]["labels"]["alertname"] == "HighLatency"


def test_a_leaking_shop_pages_on_memory_rather_than_on_errors() -> None:
    # The rule that matters for a leak, and the argument for it: by the time a
    # climbing heap moves the error rate the shop has been failing for a while
    # and the page is late. Memory is the signal that moves first.
    alert = an_alert_for(RESOURCE_LEAK, DONT_CARE_INSTANT)

    assert alert["alerts"][0]["labels"]["alertname"] == "HighMemoryUsage"


def test_a_memory_alert_says_it_is_about_a_climb() -> None:
    # A responder is being told about a trend, not an outage. "Error rate above
    # threshold" on a leak would describe an incident that is not happening yet.
    alert = an_alert_for(RESOURCE_LEAK, DONT_CARE_INSTANT)

    assert "climbing" in alert["alerts"][0]["annotations"]["summary"]


def test_the_alert_names_the_service_it_is_about() -> None:
    alert = an_alert_for(FEATURE_FLAG_TOGGLE, DONT_CARE_INSTANT)

    assert alert["alerts"][0]["labels"]["service"] == "io-shop"


def test_the_alert_says_when_it_started_the_way_the_logs_do() -> None:
    # Same timestamp format the shop's own logs and metric buckets carry, so a
    # consumer lining the alert up against them is not parsing two dialects.
    alert = an_alert_for(FEATURE_FLAG_TOGGLE, SOME_INSTANT)

    assert alert["alerts"][0]["startsAt"] == "2026-08-29T10:41:00Z"


def test_the_alert_is_firing_at_both_levels_grafana_reports_it() -> None:
    alert = an_alert_for(FEATURE_FLAG_TOGGLE, DONT_CARE_INSTANT)

    assert alert["status"] == "firing"
    assert alert["alerts"][0]["status"] == "firing"


def test_the_alert_goes_to_the_configured_webhook() -> None:
    some_webhook_url = "http://watcher.test/webhooks/alerts"
    post = an_accepting_receiver()

    fire_alert(FEATURE_FLAG_TOGGLE, settings=a_settings(some_webhook_url), post=post)

    assert post.call_args.args[0] == some_webhook_url


def test_the_incident_the_receiver_opened_is_reported_back() -> None:
    # The console has nowhere else to learn it: the incident is named by the
    # side that accepted the alert.
    some_incident_id = "incident-7f3c"
    post = an_accepting_receiver(some_incident_id)

    delivered = fire_alert(FEATURE_FLAG_TOGGLE, settings=a_settings(), post=post)

    assert delivered["incident_id"] == some_incident_id


def test_an_unreachable_receiver_is_not_reported_as_an_alert_raised() -> None:
    post = create_autospec(httpx.post)
    post.side_effect = httpx.ConnectError("connection refused")

    with pytest.raises(AlertNotDelivered):
        fire_alert(FEATURE_FLAG_TOGGLE, settings=a_settings(), post=post)


def test_a_receiver_that_rejects_the_alert_is_not_reported_as_an_alert_raised() -> None:
    # Something is listening, and it did not take the alert. An incident nobody
    # is handling would otherwise look handled.
    post = create_autospec(httpx.post)
    post.return_value = httpx.Response(
        500,
        json={"detail": "boom"},
        request=httpx.Request("POST", "http://argus.test/webhooks/alerts"),
    )

    with pytest.raises(AlertNotDelivered):
        fire_alert(FEATURE_FLAG_TOGGLE, settings=a_settings(), post=post)
