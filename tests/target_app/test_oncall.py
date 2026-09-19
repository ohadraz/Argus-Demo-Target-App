from __future__ import annotations

from datetime import UTC, datetime, timedelta

from target_app.app import MetricBucket
from target_app.oncall import (
    ACKNOWLEDGED_AFTER,
    MONITORING_NOTICES_AFTER,
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

The other half of what is covered here is the two clocks. An incident begins
when somebody was paged and ends when the telemetry stopped being troubled, and
the minutes between are what a postmortem prices. Read from the wrong ends -
oldest bucket to newest - a seven-minute outage reported an hour and a half of
somebody's attention, and it grew while nobody touched it.

Nothing needs the app or a clock: the incident is derived from metric buckets
and a moment, and a bucket is a handful of numbers and a timestamp.
"""

SOME_INCIDENT = "incident-1"

SOME_MINUTE = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

DONT_CARE_VOLUME = 1_000

# A minute the shop was broken in, and one it was well in. The calm figures are
# the generator's own baseline; the troubled ones are a third of requests
# failing, which is what the flag scenarios do.
A_TROUBLED_ERROR_RATE = 0.33
A_CALM_ERROR_RATE = 0.01
A_TROUBLED_P95_MS = 1_800
A_CALM_P95_MS = 215

# What an incident is bounded by is the error rate and the latency. Memory is
# required on a bucket and decides nothing here.
DONT_CARE_MEMORY_BYTES = 400 * 1024**2
DONT_CARE_STARTED_AT = SOME_MINUTE.timestamp()

A_RESPONDER = next(iter(RESPONDERS))
THE_SLOWEST_RESPONSE = max(ACKNOWLEDGED_AFTER.values())
THE_QUICKEST_RESPONSE = min(ACKNOWLEDGED_AFTER.values())


def test_the_incident_begins_when_somebody_was_paged() -> None:
    # Not when the shop broke. The minutes before anyone was told are minutes
    # nobody could have spent, and counting them made a seven-minute outage
    # report an hour and a half of attention.
    broke_at = SOME_MINUTE
    paged_at = broke_at + timedelta(minutes=5)

    incident = an_incident(
        SOME_INCIDENT,
        _troubled_minutes_from(broke_at, timedelta(minutes=10)),
        paged_at
    )

    assert incident is not None
    assert incident["created_at"] == _as_text(paged_at)


def test_the_incident_ends_at_the_last_minute_the_shop_was_troubled() -> None:
    # The telemetry says when it was over, and the window it is read from does
    # not: `/metrics` answers a rolling ninety minutes whether the shop is well
    # or not, so an incident read to the newest bucket never ends.
    some_trouble = timedelta(minutes=6)

    incident = an_incident(
        SOME_INCIDENT,
        _troubled_minutes_from(SOME_MINUTE, some_trouble)
        + _calm_minutes_from(SOME_MINUTE + some_trouble + timedelta(minutes=1),
                             timedelta(minutes=20)),
        SOME_MINUTE
    )

    assert incident is not None
    assert incident["resolved_at"] == _as_text(SOME_MINUTE + some_trouble)


def test_a_calm_shop_reports_the_same_incident_however_long_it_stays_up() -> None:
    # The figure has to stop moving once the shop recovers. It did not: two
    # reads of one finished incident, a minute apart, disagreed by a minute per
    # responder - which is how a postmortem and the test checking it came to
    # quote different numbers for the same night.
    some_trouble = timedelta(minutes=4)
    troubled = _troubled_minutes_from(SOME_MINUTE, some_trouble)
    a_minute_later = _calm_minutes_from(
        SOME_MINUTE + some_trouble + timedelta(minutes=1), timedelta(minutes=1)
    )

    asked_now = an_incident(SOME_INCIDENT, troubled, SOME_MINUTE)
    asked_again = an_incident(SOME_INCIDENT, troubled + a_minute_later, SOME_MINUTE)

    assert asked_now == asked_again


def test_each_responder_acknowledges_at_their_own_moment_after_the_page() -> None:
    # Two responders at different offsets is what makes person-minutes visible
    # downstream: one span could be mistaken for the incident's own length.
    paged_at = SOME_MINUTE + timedelta(minutes=3)

    incident = an_incident(
        SOME_INCIDENT,
        _troubled_minutes_from(SOME_MINUTE, timedelta(minutes=30)),
        paged_at
    )

    assert incident is not None
    assert _acknowledged_at(incident) == {
        responder: _as_text(paged_at + waited)
        for responder, waited in ACKNOWLEDGED_AFTER.items()
    }


def test_an_acknowledgement_names_its_acknowledger_and_carries_no_title() -> None:
    # The title is on the person, which is what forces the second request. An
    # acknowledgement that carried one would let an adapter be written that
    # never makes it, and then meet a real account that requires it.
    incident = an_incident(
        SOME_INCIDENT,
        _troubled_minutes_from(SOME_MINUTE, timedelta(minutes=30)),
        SOME_MINUTE
    )

    assert incident is not None
    acknowledgement = incident["acknowledgements"][0]
    assert set(acknowledgement["acknowledger"]) == {"id", "type", "summary"}
    assert "job_title" not in acknowledgement["acknowledger"]


def test_a_responder_who_would_have_acknowledged_after_it_ended_did_not() -> None:
    # An incident shorter than somebody's response time is one they were never
    # part of. Reported anyway, they would have acknowledged after it resolved
    # and spent a negative number of minutes on it.
    an_incident_over_before_anyone_picked_it_up = an_incident(
        SOME_INCIDENT,
        _troubled_minutes_from(SOME_MINUTE, THE_QUICKEST_RESPONSE / 2),
        SOME_MINUTE
    )

    assert an_incident_over_before_anyone_picked_it_up is not None
    assert an_incident_over_before_anyone_picked_it_up["acknowledgements"] == []


def test_only_the_responders_who_made_it_in_time_are_reported() -> None:
    # The quickest is in and the slowest is not, which is the case that says the
    # filter is per responder rather than all-or-nothing.
    a_span_between_the_two = (THE_QUICKEST_RESPONSE + THE_SLOWEST_RESPONSE) / 2

    incident = an_incident(
        SOME_INCIDENT,
        _troubled_minutes_from(SOME_MINUTE, a_span_between_the_two),
        SOME_MINUTE
    )

    assert incident is not None
    assert list(_acknowledged_at(incident)) == [
        responder
        for responder, waited in ACKNOWLEDGED_AFTER.items()
        if waited <= a_span_between_the_two
    ]


def test_a_shop_that_was_never_troubled_is_no_incident_at_all() -> None:
    # A calm ninety minutes is a calm ninety minutes. Nobody was paged for it,
    # and an endpoint that reported one anyway would put a postmortem's worth of
    # attention on a shop that never broke.
    assert an_incident(
        SOME_INCIDENT,
        _calm_minutes_from(SOME_MINUTE, timedelta(minutes=90)),
        SOME_MINUTE
    ) is None


def test_an_incident_nothing_alerted_through_here_is_paged_by_the_monitoring() -> None:
    # A suite drives the whole incident itself: it stages the scenario and posts
    # the alert straight at whatever is listening, so this service is never told
    # anybody was paged. The page is modelled rather than denied - an on-call
    # provider answering "nobody responded" would make the response cost a
    # property of who pressed the button.
    incident = an_incident(
        SOME_INCIDENT,
        _troubled_minutes_from(SOME_MINUTE, timedelta(minutes=30)),
        None
    )

    assert incident is not None
    assert incident["created_at"] == _as_text(SOME_MINUTE + MONITORING_NOTICES_AFTER)


def test_with_nothing_reported_there_is_no_incident_to_have_been_paged_for() -> None:
    assert an_incident(SOME_INCIDENT, [], SOME_MINUTE) is None


def test_a_responder_is_answered_with_the_job_title_the_directory_holds() -> None:
    user = a_user(A_RESPONDER)

    assert user is not None
    assert user["job_title"] == RESPONDERS[A_RESPONDER]["job_title"]


def test_a_user_nobody_holds_is_answered_as_unknown() -> None:
    assert a_user("nobody") is None


def _troubled_minutes_from(began_at: datetime, span: timedelta) -> list[MetricBucket]:
    """One broken minute a minute, from `began_at` to `began_at + span`."""
    return _minutes_from(began_at, span, A_TROUBLED_ERROR_RATE, A_TROUBLED_P95_MS)


def _calm_minutes_from(began_at: datetime, span: timedelta) -> list[MetricBucket]:
    """The same, for a shop that is working."""
    return _minutes_from(began_at, span, A_CALM_ERROR_RATE, A_CALM_P95_MS)


def _minutes_from(began_at: datetime,
                  span: timedelta,
                  error_rate: float,
                  p95_ms: int) -> list[MetricBucket]:
    return [
        MetricBucket(
            bucket_id=_as_text(began_at + timedelta(minutes=minute)),
            error_rate=error_rate,
            p50_ms=100,
            p95_ms=p95_ms,
            request_volume=DONT_CARE_VOLUME,
            memory_used_bytes=DONT_CARE_MEMORY_BYTES,
            process_start_time_seconds=DONT_CARE_STARTED_AT
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
