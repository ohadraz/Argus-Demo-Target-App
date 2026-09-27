"""What a shopper's basket comes to, which Io works out somewhere else.

Discounts are not the shop's arithmetic. Which offers a shopper qualifies for,
how they stack, and what the basket therefore comes to belongs to the pricing
service - a service the same company runs, deployed on its own, released on its
own, and on the account page's request path because the page shows the shopper
what they would pay today.

That is the difference between this module and `payment_provider`, and it is
the only difference that matters: both are somebody else's service on Io's
request path, and only one of them is somebody else's *company*. Nothing in the
host name settles it - `pricing.io-internal.svc` looks internal because somebody
chose that spelling - which is why the fact is published in the service
catalogue rather than left to be inferred here.

The shop does not retry and does not price the basket itself. What it does do is
say how long it is prepared to wait, because a dependency on the request path
that is waited on without a limit makes its latency into Io's latency, one
request at a time, until every worker is sitting in the same call. The deadline
is the shop's own and is handed to whoever reaches the service, since a socket
can only be given up where it is held.

A call that spends the whole deadline without a price is not this request's
failure and not the shop's: the page still renders, without the basket panel and
with the words for why. A call that answers - promptly or slowly - is a price,
and a slow one is remarked on.

How the service is reached is the caller's to supply, for the reason the payment
provider's is: the shop is rendered many times over to produce a minute of
telemetry, and a socket per render would be thousands of them per read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final


# Where the pricing service answers. A host rather than a team name, because a
# host is what appears in a slow call's own words - and those words are what
# somebody reading the logs has to recognise as not-this-process.
PRICING_HOST: Final = "pricing.io-internal.svc"

# What is asked for. Spelled out rather than built, so the path in a log line is
# the path the shop actually requested.
BASKET_PATH: Final = "/v1/shoppers/{shopper_id}/basket-total"

# How long a call may take before the shop says so. A client's own threshold,
# not the service's promise: Io decides what is slow enough to be worth a line
# in its own logs, and a service that has never been near this number is one
# nobody writes a line about.
SLOW_CALL_MS: Final = 200

# How long a call may take before the shop stops waiting for it. Above the
# threshold it remarks on, because a call worth a log line is not yet a call
# worth abandoning - and far below the time a page may spend, because this one
# is spent on every render and a dependency answering slowly must cost the shop
# a bounded amount rather than all of it.
PRICING_DEADLINE_MS: Final = 500


@dataclass(frozen=True)
class PricingAnswer:
    """One reply from the pricing service: what the basket comes to, and how
    long the call took.

    The duration is carried on the answer rather than timed by the shop, for the
    reason `ProviderAnswer` carries a status rather than raising on its own: what
    the shop observed about the call is the caller's to report, and turning it
    into the shop's own words is this module's job. It is also what lets a minute
    of telemetry be produced without a minute of waiting.

    A call given up on at the deadline is reported the same way as any other: no
    price, and the milliseconds that were spent before whoever held the socket
    let it go.
    """

    total_cents: int | None
    took_ms: int


@dataclass(frozen=True)
class PricedBasket:
    """A priced basket, and the words for a call that was worth remarking on.

    `slow_call` is `None` on a call that came back in the time it always does,
    and is a sentence naming the service, the path and the milliseconds when it
    did not. It sits here rather than being raised, because a slow answer is
    still an answer - the same shape as `RenderedPage.cache_failure`, which
    reports something broken underneath a page that rendered perfectly well.

    `unavailable` is the words for a call the shop stopped waiting for, and
    `total_cents` is `None` exactly when it is set. That pair is the deadline's
    whole effect: no price to show, a reason in the logs, and a page that still
    renders everything else it renders.
    """

    total_cents: int | None
    slow_call: str | None = None
    unavailable: str | None = None


class PricingServiceFailed(Exception):
    """The pricing service did not answer with a price.

    Named for what the shop can see. Io cannot tell a service that is down from
    one refusing this particular basket, and inventing the distinction in an
    exception type would be the shop claiming knowledge of a process it does not
    run - which is as true of a service down the corridor as of one in another
    company.

    Not raised for a call that ran out of time. That one the shop decided, so it
    is reported rather than raised.
    """


# How the pricing service is reached, given a shopper and the milliseconds the
# shop is willing to wait. The deadline is part of the seam rather than left to
# whoever wires it up, because a transport with no timeout is the one thing that
# turns the pricing service's latency into Io's.
type AskThePricingService = Callable[[str, int], PricingAnswer]


def basket_total(shopper_id: str,
                 ask: AskThePricingService,
                 deadline_ms: int = PRICING_DEADLINE_MS) -> PricedBasket:
    """What this shopper's basket comes to, and what the call cost to make.

    Raises `PricingServiceFailed` when the service answers without a price
    inside the deadline - that is the service refusing, and the page cannot show
    a basket it was refused. A call that spent the whole deadline is returned
    instead, with no price and the words for why: the shop gave up on it, so it
    is the shop's degradation to report rather than the service's failure to
    raise.

    A slow price is a price: it comes back with the words for how slow, so the
    shop can put them in its own logs and a reader can see where a request's time
    went without having any telemetry of the pricing service's own.

    Those words are the whole point of this function. A log line saying only
    "account page took 1900ms" describes every slow incident equally, and leaves
    a reader to guess which of the shop's own lines was the slow one - when in
    fact none of them was.
    """
    answer = ask(shopper_id, deadline_ms)
    slow_call = _slow_call_words(shopper_id, answer.took_ms)

    if answer.total_cents is None:
        if answer.took_ms >= deadline_ms:
            return PricedBasket(
                total_cents=None,
                slow_call=slow_call,
                unavailable=_deadline_words(shopper_id, deadline_ms)
            )

        raise PricingServiceFailed(
            f"{PRICING_HOST} returned no price for "
            f"{BASKET_PATH.format(shopper_id=shopper_id)}"
        )

    return PricedBasket(total_cents=answer.total_cents, slow_call=slow_call)


def _slow_call_words(shopper_id: str, took_ms: int) -> str | None:
    """What to say about a call that took too long, or nothing about one that
    did not.

    Names the service, the path and the milliseconds, in that order, because
    that is the order a reader needs them in: what was called, what was asked
    of it, and how badly it went.
    """
    if took_ms < SLOW_CALL_MS:
        return None

    return (
        f"{PRICING_HOST} took {took_ms}ms for "
        f"{BASKET_PATH.format(shopper_id=shopper_id)}"
    )


def _deadline_words(shopper_id: str, deadline_ms: int) -> str:
    """What to say about a call the shop stopped waiting for.

    Says the budget as well as the service and the path, because the number is
    Io's own choice and a reader looking at this line is deciding whether the
    dependency got slower or the budget got tighter.
    """
    return (
        f"{PRICING_HOST} did not answer within {deadline_ms}ms for "
        f"{BASKET_PATH.format(shopper_id=shopper_id)}; "
        f"the basket total was left off the page"
    )
