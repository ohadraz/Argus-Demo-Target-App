"""The shop's takings, in the shape a payment provider reports them.

Stands in for Stripe's `GET /v1/charges` the way `/argocd/{application}` stands
in for Argo CD: same wire shape, same field names, so the adapter reading it is
the same code that would read the real thing. Anything renamed here would be a
lie the adapter has to be written around.

Over a minute `/scenario/metrics` still reports, charges are derived from that very
minute, so takings and telemetry cannot disagree: a minute in which a third of
requests failed is a minute in which a third of the orders never happened. That
is the whole point of the endpoint - an incident that breaks the shop has to show
up in the money, or an estimate built on it is measuring nothing.

Over a minute older than the metrics reach, they are the shop's ordinary trade.
The invariant above is therefore about the window the incidents are in, and it
has to be: this provider does not expire charges, so an old window is an ordinary
window there. Answering it empty would say the shop took nothing, and nothing
taken and nothing recorded are opposite findings - a consumer told they are the
same prices an incident at zero and calls it a measurement. One did.

Deterministic, and derived rather than stored: the same window asked for twice
answers the same, and a scenario reseeded starts the takings over with it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from target_app.generator import (
    BASELINE_ERROR_RATE,
    REPORTED_VOLUME_PER_MINUTE,
    TIMESTAMP_FORMAT,
)

# How many of the requests that succeeded end in somebody paying. A shop's
# conversion rate, invented for the demo and fixed so a window is reproducible.
#
# Low, and deliberately so: at this shop's traffic it puts nine orders in a
# minute rather than ninety-five, and the count is what a reader of this
# endpoint pays for. A week is ten thousand minutes, so every order a minute
# gains is ten thousand charges somebody has to page through a hundred at a
# time. Nine is the floor worth stopping at - a minute in which half the
# requests failed still visibly takes fewer orders, and below about this the
# integer arithmetic flattens that to "all of them" or "none".
ORDERS_PER_SUCCESSFUL_REQUEST = 0.008

# What an order costs, in the currency's minor unit. Two prices because the
# shop trades in two currencies, and one figure covering both would let a
# consumer add them together without noticing.
#
# Ten times what they were, because the conversion rate above went down by ten
# and revenue is the product of the two. Left alone, a smaller order count
# would have made every incident cost a tenth of what it costs - and an
# estimate wrong in that direction is one that argues the outage did not
# matter.
AN_ORDER_IN_CENTS = 24_000
AN_ORDER_IN_EUROCENTS = 21_000

THE_HOME_CURRENCY = "usd"
THE_SECOND_CURRENCY = "eur"

# One order in every few is paid in the second currency. Deliberately a
# minority: the point is that a consumer summing a window without looking at
# the currency gets a figure that is wrong rather than obviously broken.
EVERY_NTH_ORDER_IS_FOREIGN = 4

# How long a minute is, for spreading a minute's orders across it.
_SECONDS_PER_MINUTE = 60

# How a charge's id is put together, and what separates the instant from the
# order within its minute. Spelled out because the id is read back as well as
# written: a cursor into this window is a charge id, and resuming from it means
# taking it apart again - see `_the_charge_identified_by`.
_CHARGE_ID_PREFIX = "ch_"
_CHARGE_ID_SEPARATOR = "_"


def a_page_of_charges(buckets: list[Any],
                      started_at: datetime,
                      ended_at: datetime,
                      limit: int,
                      after: str | None = None) -> tuple[list[dict[str, Any]], bool]:
    """One page of the charges the shop took in the window, newest first, and
    whether more of them follow.

    Newest first because that is the order the provider lists in, and a cursor
    means the same thing here as there: `after` names a charge, and the page
    resumes at the one *following* it in that order - which is the one before
    it in time. A stand-in that listed oldest first would read identically to a
    consumer that sums a whole window and differently to every consumer that
    stops early, which is the kind of difference that is found in production.

    A window of any age, and that is the whole reason this is a page rather than
    a list. Where a minute was reported in the metrics, its charges come from
    what the shop actually served, so takings and telemetry cannot disagree
    about an incident. Where it was not, the shop reports the ordinary trade of
    a minute nobody has anything to say about.

    Because Stripe does not expire charges. A window older than the metrics
    reach is not a quiet window there, it is a window like any other - and a
    stand-in that answered it empty would be teaching a consumer that no
    takings and no records are the same answer, which is the one confusion this
    fixture must not plant. That distinction cost a postmortem a fabricated
    loss of nothing once already.

    The page is generated rather than sliced out of the whole window, and the
    difference is the difference between answering a week and not. A week is ten
    thousand minutes and ninety thousand charges; built whole, per request, to
    hand back a hundred of them, it is not an answer anybody can wait for.
    Walking the minutes costs one step each and the charges are composed only
    for the page, so a window's age decides how long this takes and its length
    does not.
    """
    reported = {
        minute: bucket
        for bucket in buckets
        if (minute := _minute_of(bucket.bucket_id)) is not None
    }
    resume_after = _the_charge_identified_by(after)
    page: list[dict[str, Any]] = []
    oldest = started_at.replace(second=0, microsecond=0)
    # From the cursor's own minute rather than from the newest end of the
    # window, and that is what makes the cost of a page the same wherever in
    # the window it falls. Walked from the end each time, every page steps over
    # every charge ahead of it, so paging a week costs the square of a week -
    # the very shape this function exists to avoid, reintroduced one loop
    # further in.
    minute = ended_at.replace(second=0, microsecond=0)

    if resume_after is not None:
        minute = min(minute, resume_after[0])

    while minute >= oldest:
        taken = _orders_taken_in(minute, reported)

        # Backwards within the minute too, because newest first is an order over
        # charges and not only over minutes.
        for order in reversed(range(taken)):
            # Only the cursor's own minute can hold charges ahead of it: every
            # later one was left behind by where the walk started.
            if (resume_after is not None
                    and minute == resume_after[0]
                    and order >= resume_after[1]):
                continue

            if len(page) == limit:
                return page, True

            page.append(_a_charge(minute, order, taken))

        minute -= timedelta(minutes=1)

    return page, False


def _orders_taken_in(minute: datetime, reported: dict[datetime, Any]) -> int:
    """How many orders the shop took in this minute.

    From the minute's own reading where the metrics still hold one, so a minute
    in which a third of requests failed is a minute in which a third of the
    orders never happened. From the shop's ordinary trade where they do not,
    which is what a minute older than the metrics reach was: nothing was wrong
    with the shop then, and nothing recorded it because nothing had to.

    The resting error rate rather than that minute's own wobble, because the
    wobble is the generator's and is gone with the bucket. What is left is the
    honest shape of an ordinary minute rather than a reconstruction of one.
    """
    bucket = reported.get(minute)
    volume = bucket.request_volume if bucket is not None else REPORTED_VOLUME_PER_MINUTE
    error_rate = bucket.error_rate if bucket is not None else BASELINE_ERROR_RATE

    return int(volume * (1.0 - error_rate) * ORDERS_PER_SUCCESSFUL_REQUEST)


def _the_charge_identified_by(after: str | None) -> tuple[datetime, int] | None:
    """The minute and order a cursor names, or `None` where it names none.

    Read back out of the id rather than looked up, which is what lets a page be
    generated instead of found: a cursor into a million charges that had to be
    searched for would put the whole window back in front of every request.

    `None` for anything unreadable, which starts the window at its beginning.
    A cursor from another window or another shop is a caller's mistake, and the
    first page is the answer least likely to be quietly wrong.
    """
    if after is None:
        return None

    _, _, tail = after.partition(_CHARGE_ID_PREFIX)
    seconds, separator, order = tail.partition(_CHARGE_ID_SEPARATOR)

    if not separator:
        return None

    try:
        return datetime.fromtimestamp(int(seconds), tz=UTC), int(order)
    except (ValueError, OSError, OverflowError):
        return None


def _a_charge(minute: datetime, order: int, orders_in_the_minute: int) -> dict[str, Any]:
    """One charge, in the provider's own wire shape.

    Only the fields a consumer of takings has any business reading. A real
    charge carries forty more, and a stand-in inventing plausible values for
    all of them would be inviting a consumer to depend on them.

    Spread across the minute it was taken in rather than a second apart, and
    that is a correctness matter rather than a cosmetic one. A shop busy enough
    to take more than sixty orders a minute - which this one was until the
    conversion rate came down - put the later ones into the *next* minute under
    a second per order, which meant a window answered charges `created` outside
    the window that was asked for, and a listing whose order did not follow the
    field the provider orders by. Both are lies about the wire, and both were
    invisible while nobody paged far enough to notice. Spreading them keeps
    that true at any order count rather than at the current one.

    The id is built from the minute rather than from the instant, so that a
    cursor can be taken apart again without knowing how many orders that minute
    held - see `_the_charge_identified_by`.
    """
    foreign = order % EVERY_NTH_ORDER_IS_FOREIGN == 0
    at = minute + timedelta(
        seconds=order * _SECONDS_PER_MINUTE // max(orders_in_the_minute, 1)
    )

    return {
        "id": (
            f"{_CHARGE_ID_PREFIX}{int(minute.timestamp())}"
            f"{_CHARGE_ID_SEPARATOR}{order}"
        ),
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
