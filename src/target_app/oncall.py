"""Who was paged for the incident, in the shape an on-call provider reports it.

Stands in for PagerDuty's `GET /incidents/{id}` and `GET /users/{id}` the way
`/stripe/v1/charges` stands in for Stripe: same wire shape, same field names,
same envelope, so the adapter reading it is the same code that would read the
real thing.

Two resources rather than one, deliberately. PagerDuty puts the acknowledgement
on the incident and the job title on the person, and a stand-in that answered
both in a single payload would let an adapter be written that never makes the
second request - and then meet a real account that requires it.

The response is derived from the same minutes `/metrics` reports, so the
incident somebody was paged for is the incident the telemetry describes. The
acknowledgements are authored: this service knows when its own incident began,
and the demo needs somebody to have picked it up a few minutes later.
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

# How long each of them took to acknowledge. Different, so a reader can see
# that the minutes are counted from each person's own moment; and both a few
# minutes in, because the gap between an alert firing and somebody picking it
# up is the thing this endpoint exists to make measurable.
ACKNOWLEDGED_AFTER: dict[str, timedelta] = {
    "PDUSERA": timedelta(minutes=4),
    "PDUSERB": timedelta(minutes=9)
}

# PagerDuty's own vocabulary for what a resource is.
_A_USER_REFERENCE = "user_reference"
_RESOLVED = "resolved"


def an_incident(incident_id: str, buckets: list[Any]) -> dict[str, Any] | None:
    """The incident as the on-call provider holds it, or `None` if there is none.

    The window comes from the telemetry: the first minute the service reported
    is when the incident began, and the last is when it ended. A responder who
    would have acknowledged after it was over did not acknowledge it at all,
    which is what keeps a short scenario from reporting negative attention.
    """
    minutes = sorted(
        minute for minute in (_minute_of(bucket.bucket_id) for bucket in buckets)
        if minute is not None
    )

    if not minutes:
        return None

    began_at, ended_at = minutes[0], minutes[-1]

    return {
        "id": incident_id,
        "type": "incident",
        "summary": "Elevated error rate on checkout",
        "title": "Elevated error rate on checkout",
        "status": _RESOLVED,
        "created_at": _as_text(began_at),
        "resolved_at": _as_text(ended_at),
        "last_status_change_at": _as_text(ended_at),
        "acknowledgements": [
            _an_acknowledgement(responder, began_at + waited)
            for responder, waited in ACKNOWLEDGED_AFTER.items()
            if began_at + waited <= ended_at
        ]
    }


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
