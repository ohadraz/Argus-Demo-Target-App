from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import create_autospec

import httpx
import pytest

from io_shop.spend_reconciliation import DisagreeingAccount, Reconciliation
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
    SILENT_DATA_CORRUPTION,
)

"""What the shop's monitoring promises about the alert it raises.

The webhook goes to something outside this repo, so every test here drives a
doubled transport. What matters is the payload's shape - a consumer parses it
with the code it points at a real Grafana - and that a delivery which did not
happen is never reported as one.
"""

SOME_INSTANT = datetime(2026, 8, 29, 10, 41, 0, tzinfo=UTC)
DONT_CARE_INSTANT = SOME_INSTANT

# A purchase recorded a week before the check that found it, and deliberately not
# on a minute boundary: a measured onset is always a bucket id and this one never
# is, because it is the instant a shopper bought something rather than a minute
# somebody aggregated.
AN_OLD_PURCHASE = datetime(2026, 9, 22, 9, 19, 43, tzinfo=UTC)

# How many accounts the check looked at. Larger than any count of disagreements
# below, because a count of disagreements says nothing without it.
SOME_ACCOUNTS_CHECKED = 400


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


def a_finding(accounts: int,
              largest_gap_cents: int = 129_731,
              oldest_affected_purchase_at: datetime | None = AN_OLD_PURCHASE
              ) -> Reconciliation:
    """What the shop's integrity check reported, with `accounts` disagreeing.

    Built rather than run, because what these cases are about is the payload: the
    check's own arithmetic is covered where it lives, and a test that ran the check
    here would be asserting a figure it did not choose.
    """
    return Reconciliation(
        accounts_checked=SOME_ACCOUNTS_CHECKED,
        accounts_that_disagree=tuple(
            DisagreeingAccount(
                shopper_id=f"shopper-{index}",
                gap_cents=largest_gap_cents,
                oldest_affected_purchase_at=oldest_affected_purchase_at,
            )
            for index in range(accounts)
        ),
    )


def test_totals_that_do_not_reconcile_fire_a_rule_of_their_own() -> None:
    # The one rule here not written against a series. No threshold on any metric
    # was crossed, because none moved.
    alert = an_alert_for(SILENT_DATA_CORRUPTION, DONT_CARE_INSTANT, a_finding(240))

    assert alert["alerts"][0]["labels"]["alertname"] == "SpendTotalsDoNotReconcile"


def test_the_finding_is_paged_about_as_the_shop_rather_than_as_a_scenario() -> None:
    # The service and the severity are the ones every other alert carries: a
    # consumer routes on these, and an incident that arrived labelled differently
    # would be an incident about a different service.
    alert = an_alert_for(SILENT_DATA_CORRUPTION, DONT_CARE_INSTANT, a_finding(240))

    assert alert["alerts"][0]["labels"]["service"] == "io-shop"
    assert alert["alerts"][0]["labels"]["severity"] == "critical"


def test_the_summary_says_how_many_totals_disagree() -> None:
    alert = an_alert_for(SILENT_DATA_CORRUPTION, DONT_CARE_INSTANT, a_finding(240))

    assert "240 of 400" in alert["alerts"][0]["annotations"]["summary"]


def test_the_summary_says_the_widest_gap_as_money() -> None:
    # A responder reads this. The gap is cents everywhere else, because that is
    # what a price is stored in.
    alert = an_alert_for(
        SILENT_DATA_CORRUPTION, DONT_CARE_INSTANT, a_finding(240, 129_731)
    )

    assert "1,297.31" in alert["alerts"][0]["annotations"]["summary"]


def test_the_summary_says_when_the_oldest_affected_purchase_was_recorded() -> None:
    # The only figure in the incident that dates the fault, said in the prose as
    # well as in the annotation - a reader gets the sentence, a consumer gets the
    # field, and neither has to parse the other's.
    alert = an_alert_for(SILENT_DATA_CORRUPTION, DONT_CARE_INSTANT, a_finding(240))

    assert "2026-09-22T09:19:43Z" in alert["alerts"][0]["annotations"]["summary"]


def test_the_onset_is_carried_as_a_field_of_its_own() -> None:
    # Its own annotation, because a consumer has to do arithmetic with it. Parsing
    # it back out of a sentence would make the prose a wire format.
    alert = an_alert_for(SILENT_DATA_CORRUPTION, DONT_CARE_INSTANT, a_finding(240))

    assert alert["alerts"][0]["annotations"]["onset"] == "2026-09-22T09:19:43Z"


def test_when_the_check_ran_is_not_when_the_writing_went_wrong() -> None:
    # The whole reason the onset is carried separately. This check runs weekly, so
    # `startsAt` is up to a week later than the fault - and a consumer that took
    # it for the onset would anchor the incident on the wrong week.
    alert = an_alert_for(SILENT_DATA_CORRUPTION, SOME_INSTANT, a_finding(240))

    assert alert["alerts"][0]["startsAt"] == "2026-08-29T10:41:00Z"
    assert alert["alerts"][0]["annotations"]["onset"] != (
        alert["alerts"][0]["startsAt"]
    )


def test_a_finding_that_cannot_be_dated_carries_no_onset_at_all() -> None:
    # Absent rather than empty. A consumer reading an onset it can act on must not
    # also have to decide whether a blank one means "now".
    alert = an_alert_for(
        SILENT_DATA_CORRUPTION,
        DONT_CARE_INSTANT,
        a_finding(240, oldest_affected_purchase_at=None),
    )

    assert "onset" not in alert["alerts"][0]["annotations"]


def test_a_handful_of_disagreeing_totals_pages_nobody_about_reconciliation() -> None:
    # Not one, and not a handful. A total that does not add up is a support ticket
    # and a correction; a rule that fired on it would fire most weeks, and what is
    # worth waking somebody for is a population of them.
    alert = an_alert_for(FEATURE_FLAG_TOGGLE, DONT_CARE_INSTANT, a_finding(3))

    assert alert["alerts"][0]["labels"]["alertname"] == "HighErrorRate"


def test_a_check_that_found_nothing_leaves_the_ordinary_alert_alone() -> None:
    # The shop's integrity job runs whatever is staged, so every alert is offered
    # its finding. A quiet answer has to decide nothing.
    alert = an_alert_for(BAD_DEPLOYMENT, DONT_CARE_INSTANT, a_finding(0))

    assert alert["alerts"][0]["labels"]["alertname"] == "HighLatency"


def test_an_alert_raised_without_a_finding_is_the_one_it_always_was() -> None:
    # Nothing asked the check. Every caller that predates it still gets the rule
    # its scenario trips.
    alert = an_alert_for(RESOURCE_LEAK, DONT_CARE_INSTANT)

    assert alert["alerts"][0]["labels"]["alertname"] == "HighMemoryUsage"
