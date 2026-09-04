"""The shop's takings, in the shape a payment provider reports them.

Stands in for Stripe's `GET /v1/charges` the way `/argocd/{application}` stands
in for Argo CD: same wire shape, same field names, so the adapter reading it is
the same code that would read the real thing. Anything renamed here would be a
lie the adapter has to be written around.

Charges are derived from the very minutes `/metrics` reports, so takings and
telemetry cannot disagree: a minute in which a third of requests failed is a
minute in which a third of the orders never happened. That is the whole point
of the endpoint - an incident that breaks the shop has to show up in the money,
or an estimate built on it is measuring nothing.

Deterministic, and derived rather than stored: the same window asked for twice
answers the same, and a scenario reseeded starts the takings over with it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from target_app.generator import TIMESTAMP_FORMAT

# How many of the requests that succeeded end in somebody paying. A shop's
# conversion rate, invented for the demo and fixed so a window is reproducible.
ORDERS_PER_SUCCESSFUL_REQUEST = 0.08

# What an order costs, in the currency's minor unit. Two prices because the
# shop trades in two currencies, and one figure covering both would let a
# consumer add them together without noticing.
AN_ORDER_IN_CENTS = 2_400
AN_ORDER_IN_EUROCENTS = 2_100

THE_HOME_CURRENCY = "usd"
THE_SECOND_CURRENCY = "eur"

# One order in every few is paid in the second currency. Deliberately a
# minority: the point is that a consumer summing a window without looking at
# the currency gets a figure that is wrong rather than obviously broken.
EVERY_NTH_ORDER_IS_FOREIGN = 4


def charges_between(buckets: list[Any],
                    started_at: datetime,
                    ended_at: datetime) -> list[dict[str, Any]]:
    """Every charge the shop took in the window, oldest first.

    Takes the metric buckets rather than reading them itself, so this stays a
    rendering of what the shop already reports and cannot drift from it.
    """
    charges: list[dict[str, Any]] = []

    for bucket in buckets:
        minute = _minute_of(bucket.bucket_id)

        if minute is None or not started_at <= minute <= ended_at:
            continue

        succeeded = bucket.request_volume * (1.0 - bucket.error_rate)

        for order in range(int(succeeded * ORDERS_PER_SUCCESSFUL_REQUEST)):
            charges.append(_a_charge(minute, order))

    return charges


def _a_charge(minute: datetime, order: int) -> dict[str, Any]:
    """One charge, in the provider's own wire shape.

    Only the fields a consumer of takings has any business reading. A real
    charge carries forty more, and a stand-in inventing plausible values for
    all of them would be inviting a consumer to depend on them.
    """
    foreign = order % EVERY_NTH_ORDER_IS_FOREIGN == 0
    at = minute + timedelta(seconds=order)

    return {
        "id": f"ch_{int(at.timestamp())}_{order}",
        "object": "charge",
        "amount": AN_ORDER_IN_EUROCENTS if foreign else AN_ORDER_IN_CENTS,
        "amount_refunded": 0,
        "currency": THE_SECOND_CURRENCY if foreign else THE_HOME_CURRENCY,
        "created": int(at.timestamp()),
        "status": "succeeded",
        "paid": True,
        "refunded": False,
        "livemode": False,
    }


def _minute_of(bucket_id: str) -> datetime | None:
    """The instant a bucket id names, or `None` if it names none.

    A bucket whose id cannot be read is skipped rather than raising: this feeds
    a demo, and a console that fails to render because one minute was malformed
    is worse than one short minute.
    """
    try:
        return datetime.strptime(bucket_id, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
