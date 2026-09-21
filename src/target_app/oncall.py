"""Who was paged for the incident, in the shape an on-call provider reports it.

Stands in for PagerDuty's `GET /incidents/{id}` and `GET /users/{id}` the way
`/stripe/v1/charges` stands in for Stripe: same wire shape, same field names,
same envelope, so the adapter reading it is the same code that would read the
real thing.

Two resources rather than one, deliberately. PagerDuty puts the acknowledgement
on the incident and the job title on the person, and a stand-in that answered
both in a single payload would let an adapter be written that never makes the
second request - and then meet a real account that requires it.

The incident is bounded by two different things, and that is deliberate. It
begins when the shop's monitoring paged somebody, because that is the first
moment anyone could have responded; it ends at the last minute `/metrics`
reports as troubled, because that is when the shop was well again. The
acknowledgements are authored - the demo needs somebody to have picked it up -
and they are placed after the page, never after the breakage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from target_app.generator import TIMESTAMP_FORMAT

# Who carries the pager here. Two of them, because engagement is measured in
# person-minutes and one responder cannot show the difference between that and
# a wall-clock span. The titles are what a real directory holds - free text
# somebody typed - and are what an HR system would be asked to price.
RESPONDERS: dict[str, dict[str, str]] = {
    "PDUSERA": {
        "name": "Dana Ashworth",
        "email": "dana.ashworth@io-shop.example",
        "job_title": "Senior Software Engineer"
    },
    "PDUSERB": {
        "name": "Marco Silva",
        "email": "marco.silva@io-shop.example",
        "job_title": "Site Reliability Engineer"
    }
}

# How long after the page each of them took to pick it up. From the page and
# not from the onset: the shop is usually broken for some minutes before
# monitoring says so, and nobody can respond to something they have not been
# told about. Different from each other, so a reader can see that the minutes
# are counted from each person's own moment; and both close behind the page,
# because an incident that is over in minutes is one a slower responder never
# joins at all.
ACKNOWLEDGED_AFTER: dict[str, timedelta] = {
    "PDUSERA": timedelta(minutes=1),
    "PDUSERB": timedelta(minutes=2)
}

# What makes a minute one of the incident's rather than one of the day's. Clear
# of the baseline's own wobble on either measure - a calm minute is about 1%
# errors at 215ms - and under every staged phase, the mildest of which is 900ms
# while its error rate is still ordinary. Either alone is enough: the scenario
# that breaks by latency never moves the error rate, and the ones that break by
# errors never move the latency.
_A_TROUBLED_ERROR_RATE = 0.05
_A_TROUBLED_P95_MS = 400
# And the tail, because an incident reaching a few requests in a hundred is
# below the p95 by arithmetic and fails nothing - so a provider reading the two
# measures above would hold no incident at all for it, and every figure counted
# from person-minutes would be counted over a night nobody was woken for.
#
# Twice the quiet tail and under every staged phase: a calm minute reports
# about 380ms, and the mildest minute of an incident that lives here reports
# well over a second.
_A_TROUBLED_P99_MS = 800

# How long monitoring takes to notice, where nothing fired an alert through this
# service. A suite drives the whole incident itself - it stages the scenario and
# posts the alert straight at whatever is listening - so this service is never
# told that anybody was paged, and an on-call provider that answered "nobody
# was" would make the response cost a property of who pressed the button.
#
# A minute, because the shop's metrics are per minute and a rule that fires on
# one bad minute cannot fire sooner than the minute after it.
MONITORING_NOTICES_AFTER = timedelta(minutes=1)

# PagerDuty's own vocabulary for what a resource is.
_A_USER_REFERENCE = "user_reference"
_RESOLVED = "resolved"


def an_incident(incident_id: str,
                buckets: list[Any],
                alerted_at: datetime | None) -> dict[str, Any] | None:
    """The incident as the on-call provider holds it, or `None` if there is none.

    Two clocks, and keeping them apart is the whole of this. The page is when
    somebody was told, and it is where every responder's minutes start: an
    incident nobody has been alerted on has no responders, however long the shop
    has been broken. Recovery is the last minute the telemetry was troubled, and
    it is what those minutes are counted to.

    Neither end is "the oldest thing I can see". The metrics endpoint answers a
    rolling window whether the shop is well or not, so a window read as the
    incident makes the response look ninety minutes long on a seven-minute
    outage - and grows by one minute per responder per minute, so that two reads
    of the same finished incident disagree.

    `alerted_at` is when an alert was actually fired through this service, which
    is how the demo runs it. A suite drives the incident itself and posts its
    alert straight at Argus, so there is nothing to record - and there the page
    is modelled: monitoring notices a minute after the shop breaks.

    A responder who would have acknowledged after recovery did not acknowledge
    it at all, which is what keeps a short incident from reporting negative
    attention.
    """
    troubled = sorted(
        minute
        for minute in (
            _minute_of(bucket.bucket_id) for bucket in buckets if _is_troubled(bucket)
        )
        if minute is not None
    )

    if not troubled:
        return None

    paged_at = alerted_at or troubled[0] + MONITORING_NOTICES_AFTER
    ended_at = max(troubled[-1], paged_at)

    return {
        "id": incident_id,
        "type": "incident",
        "summary": "Elevated error rate on checkout",
        "title": "Elevated error rate on checkout",
        "status": _RESOLVED,
        "created_at": _as_text(paged_at),
        "resolved_at": _as_text(ended_at),
        "last_status_change_at": _as_text(ended_at),
        "acknowledgements": [
            _an_acknowledgement(responder, paged_at + waited)
            for responder, waited in ACKNOWLEDGED_AFTER.items()
            if paged_at + waited <= ended_at
        ]
    }


def _is_troubled(bucket: Any) -> bool:
    """Whether this is a minute the shop was broken in.

    Any of the three, because the scenarios break in different ways: one climbs
    in latency while its error rate stays ordinary, others throw errors at a
    latency nobody would notice, and one is slow for so few requests that it
    shows up in neither.
    """
    return (
        bucket.error_rate > _A_TROUBLED_ERROR_RATE
        or bucket.p95_ms > _A_TROUBLED_P95_MS
        or bucket.p99_ms > _A_TROUBLED_P99_MS
    )


def a_user(user_id: str) -> dict[str, Any] | None:
    """One responder as the provider holds them, or `None` if unknown."""
    responder = RESPONDERS.get(user_id)

    if responder is None:
        return None

    return {
        "id": user_id,
        "type": "user",
        "summary": responder["name"],
        "name": responder["name"],
        "email": responder["email"],
        "job_title": responder["job_title"],
        "role": "user",
        "time_zone": "UTC"
    }


def _an_acknowledgement(responder: str, at: datetime) -> dict[str, Any]:
    """One acknowledgement, naming its acknowledger rather than carrying them.

    A reference is all PagerDuty puts here - an id, a type and a summary - and
    reproducing that is what forces the second request for anything more.
    """
    return {
        "at": _as_text(at),
        "acknowledger": {
            "id": responder,
            "type": _A_USER_REFERENCE,
            "summary": RESPONDERS[responder]["name"]
        }
    }


def _as_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def _minute_of(bucket_id: str) -> datetime | None:
    """The instant a bucket id names, or `None` if it names none.

    A bucket whose id cannot be read is skipped rather than raising: this feeds
    a demo, and an incident that fails to answer because one minute was
    malformed is worse than one short minute.
    """
    try:
        return datetime.strptime(bucket_id, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
