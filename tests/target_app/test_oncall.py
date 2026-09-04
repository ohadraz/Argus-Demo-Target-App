from __future__ import annotations

from datetime import UTC, datetime, timedelta

from target_app.app import MetricBucket
from target_app.oncall import (
    ACKNOWLEDGED_AFTER,
    RESPONDERS,
    a_user,
    an_incident,
)

"""Who was paged, in an on-call provider's shape.

The regression net for an endpoint whose whole job is to be believed by a
vendor's SDK: the fields are PagerDuty's, the acknowledgement names its
acknowledger rather than carrying them, and the job title is somewhere else
entirely. Renaming any of that here would break the adapter and nothing in
this repo.

Nothing needs the app or a clock - the incident is derived from metric buckets,
and a bucket is four numbers and a timestamp.
"""

SOME_INCIDENT = "incident-1"

SOME_MINUTE = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

DONT_CARE_VOLUME = 1_000
DONT_CARE_ERROR_RATE = 0.5

A_RESPONDER = next(iter(RESPONDERS))


def test_the_incident_runs_from_the_first_minute_reported_to_the_last() -> None:
    # The telemetry is the incident. An endpoint that answered a window of its
    # own would let the minutes people spent and the minutes the shop was
    # broken describe two different events.
    some_span = timedelta(minutes=30)

    incident = an_incident(SOME_INCIDENT, _minutes_from(SOME_MINUTE, some_span))

    assert incident is not None
    assert incident["created_at"] == _as_text(SOME_MINUTE)
    assert incident["resolved_at"] == _as_text(SOME_MINUTE + some_span)


def test_each_responder_acknowledges_at_their_own_moment_after_it_began() -> None:
    # Two responders at different offsets is what makes person-minutes visible
    # downstream: one span could be mistaken for the incident's own length.
    dont_care_span = timedelta(minutes=30)

    incident = an_incident(SOME_INCIDENT,
                           _minutes_from(SOME_MINUTE, dont_care_span))

    assert incident is not None
    assert _acknowledged_at(incident) == {
        responder: _as_text(SOME_MINUTE + waited)
        for responder, waited in ACKNOWLEDGED_AFTER.items()
    }


def test_an_acknowledgement_names_its_acknowledger_and_carries_no_title() -> None:
    # The title is on the person, which is what forces the second request. An
    # acknowledgement that carried one would let an adapter be written that
    # never makes it, and then meet a real account that requires it.
    dont_care_span = timedelta(minutes=30)

    incident = an_incident(SOME_INCIDENT,
                           _minutes_from(SOME_MINUTE, dont_care_span))

    assert incident is not None
    acknowledgement = incident["acknowledgements"][0]
    assert set(acknowledgement["acknowledger"]) == {"id", "type", "summary"}
    assert "job_title" not in acknowledgement["acknowledger"]


def test_a_responder_who_would_have_acknowledged_after_it_ended_did_not() -> None:
    # A scenario shorter than somebody's response time is a scenario they were
    # never paged for. Reported anyway, they would have acknowledged after the
    # incident resolved and spent a negative number of minutes on it.
    a_span_shorter_than_every_response = min(ACKNOWLEDGED_AFTER.values()) \
        - timedelta(minutes=1)

    incident = an_incident(
        SOME_INCIDENT,
        _minutes_from(SOME_MINUTE, a_span_shorter_than_every_response))

    assert incident is not None
    assert incident["acknowledgements"] == []


def test_with_nothing_reported_there_is_no_incident_to_have_been_paged_for() -> None:
    assert an_incident(SOME_INCIDENT, []) is None


def test_a_responder_is_answered_with_the_job_title_the_directory_holds() -> None:
    user = a_user(A_RESPONDER)

    assert user is not None
    assert user["job_title"] == RESPONDERS[A_RESPONDER]["job_title"]


def test_a_user_nobody_holds_is_answered_as_unknown() -> None:
    assert a_user("nobody") is None


def _minutes_from(began_at: datetime, span: timedelta) -> list[MetricBucket]:
    """One bucket a minute, from `began_at` to `began_at + span` inclusive."""
    return [
        MetricBucket(
            bucket_id=_as_text(began_at + timedelta(minutes=minute)),
            error_rate=DONT_CARE_ERROR_RATE,
            p50_ms=100,
            p95_ms=400,
            request_volume=DONT_CARE_VOLUME
        )
        for minute in range(int(span // timedelta(minutes=1)) + 1)
    ]


def _acknowledged_at(incident: dict) -> dict[str, str]:
    return {
        acknowledgement["acknowledger"]["id"]: acknowledgement["at"]
        for acknowledgement in incident["acknowledgements"]
    }


def _as_text(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
