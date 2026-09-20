"""The alerting half of the shop's monitoring.

A real service does not tell anyone it is unwell. Something watches it - a
metrics stack with alert rules - and *that* is what posts a webhook when a rule
fires. So the alert is sent from here, one server reaching another, rather than
from the console page in someone's browser. Nothing about a Grafana webhook is
browser-shaped: it has no origin, no preflight, and it fires whether or not a
human has the page open.

Kept in its own module for the same reason it used to live in the page: the
shop's own request path carries no reference to the tool watching it. This is
the monitoring stack, hosted inside the fixture for convenience, not a feature
of the shop.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from typing import Any

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from target_app.scenarios import (
    BAD_DEPLOYMENT,
    CACHE_MISCONFIGURED,
    RESOURCE_LEAK,
    TIMESTAMP_FORMAT,
    utc_now,
)

HttpPost = Callable[..., httpx.Response]

# The receiver runs the whole incident - investigation, mitigation, and the
# verification wait after the action - before it answers, so this waits far
# longer than an HTTP call normally should. A real alerting stack would fire and
# forget; this one holds on because the console has nothing else to report the
# incident id from.
REQUEST_TIMEOUT_SECONDS = 300.0

# The service these alerts are about, as its logs and metrics name it.
SERVICE_NAME = "io-shop"

# Which rule fired. A deploy that slowed the shop down trips a different rule
# than one that made it throw, and the alert name is the only part of the
# payload that says which.
_HIGH_LATENCY = "HighLatency"
_HIGH_ERROR_RATE = "HighErrorRate"
_HIGH_MEMORY_USAGE = "HighMemoryUsage"

# The rule each scenario trips, and what it says. Anything not named here is
# paging about an error rate, which is what most of these scenarios break.
#
# The leak's rule is the one worth arguing about: it pages on memory, because
# by the time a leak moves the error rate the shop has been failing for a while
# and the alert is late. Its summary says so - the responder is being told
# about a climb, not an outage.
_WHAT_FIRED: dict[str, tuple[str, str]] = {
    BAD_DEPLOYMENT: (_HIGH_LATENCY, "p95 latency above threshold for 5m"),
    # The median rather than the tail, and that is the scenario rather than a
    # detail: nine requests in ten were served from cache, so the tail always
    # described a recomputed page and barely moves when the cache goes. A rule
    # written against p95 - which is how most latency alerting is written -
    # would never fire on this at all.
    CACHE_MISCONFIGURED: (
        _HIGH_LATENCY, "p50 latency above threshold for 5m"
    ),
    RESOURCE_LEAK: (
        _HIGH_MEMORY_USAGE,
        "Memory usage climbing against the container limit for 15m",
    ),
}
_BY_DEFAULT = (_HIGH_ERROR_RATE, "Error rate above threshold for 5m")


class MonitoringSettings(BaseSettings):
    """Where this service's monitoring sends the alerts it raises.

    The default is Argus on the host, which is where it runs in the documented
    setup. From inside the container the host is not `localhost`, so
    `docker-compose.yml` overrides this.
    """

    model_config = SettingsConfigDict(env_prefix="MONITORING_", extra="ignore")

    alert_webhook_url: str = Field(default="http://localhost:8000/webhooks/alerts")


@lru_cache
def get_monitoring_settings() -> MonitoringSettings:
    return MonitoringSettings()


class AlertNotDelivered(Exception):
    """The alert could not be handed to whatever is listening for it.

    Raised rather than swallowed, so the console can say the scenario is staged
    but nobody was told - the incident is real either way, and a staging that
    silently alerted nobody looks exactly like one that did.
    """


def an_alert_for(scenario_id: str | None, at: datetime) -> dict[str, Any]:
    """The firing alert, in Grafana Alertmanager's own webhook shape.

    Deliberately the vendor's shape, field nesting included: the consumer parses
    this with the same code it would point at a real Grafana, so a friendlier
    payload here would be a lie that consumer would have to be written around.
    """
    alertname, summary = _WHAT_FIRED.get(scenario_id or "", _BY_DEFAULT)

    return {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "service": SERVICE_NAME,
                    "severity": "critical",
                },
                "annotations": {"summary": summary},
                "startsAt": at.strftime(TIMESTAMP_FORMAT),
            }
        ],
    }


def fire_alert(
    scenario_id: str | None,
    settings: MonitoringSettings | None = None,
    post: HttpPost = httpx.post,
    now: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    """Posts the firing alert to the configured webhook and returns what came
    back, so a caller can report the incident it started.

    A non-2xx answer is a failure to deliver, not a delivered alert: something
    is listening at that URL but did not accept the alert, and reporting that as
    raised would leave an incident nobody is handling looking handled.
    """
    resolved = settings if settings is not None else get_monitoring_settings()
    url = resolved.alert_webhook_url

    try:
        response = post(
            url,
            json=an_alert_for(scenario_id, now()),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
    except Exception as error:
        raise AlertNotDelivered(f"could not raise an alert at [{url}]: {error}") from error

    return body
