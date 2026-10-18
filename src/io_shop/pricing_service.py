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

The shop does not retry and does not price the basket itself when the service
is slow, and it says how long each call took. It does not wait for ever, either:
a call that has not answered within `PRICING_DEADLINE_MS` is given up on and
treated as no price, because the page is worth showing without its basket total
and is not worth holding hostage to it.

How the service is reached is the caller's to supply.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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

# How long the shop waits for a price at all. Past this the basket total is
# given up on and the page renders without it, so a slow pricing service costs
# the account page at most this much rather than however long pricing takes.
PRICING_DEADLINE_MS: Final = 500

# Where pricing calls run, so the page can stop waiting for one. Bounded, so a
# pricing service that hangs outright queues calls - which then miss their
# deadline and are cancelled - rather than growing threads without limit.
_PRICING_CALLS: Final = ThreadPoolExecutor(
    max_workers=16, thread_name_prefix="pricing-call"
)


@dataclass(frozen=True)
class PricingAnswer:
    """One reply from the pricing service: what the basket comes to, and how
    long the call took.

    The duration is carried on the answer rather than timed by the shop, for the
    reason `ProviderAnswer` carries a status rather than raising on its own: what
    the shop observed about the call is the caller's to report, and turning it
    into the shop's own words is this module's job.
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
    """

    total_cents: int
    slow_call: str | None = None


class PricingServiceFailed(Exception):
    """The pricing service did not answer with a price.

    Named for what the shop can see. Io cannot tell a service that is down from
    one refusing this particular basket, and inventing the distinction in an
    exception type would be the shop claiming knowledge of a process it does not
    run - which is as true of a service down the corridor as of one in another
    company.
    """


# How the pricing service is reached, given a shopper.
type AskThePricingService = Callable[[str], PricingAnswer]


def basket_total(shopper_id: str, ask: AskThePricingService) -> PricedBasket:
    """What this shopper's basket comes to, and what the call cost to make.

    Raises `PricingServiceFailed` for anything that is not a price - including
    no answer within `PRICING_DEADLINE_MS`. A slow price inside the deadline is
    a price: it comes back with the words for how slow, so the shop can put
    them in its own logs and a reader can see where a request's time went
    without having any telemetry of the pricing service's own.

    Those words are the whole point of this function. A log line saying only
    "account page took 1900ms" describes every slow incident equally, and leaves
    a reader to guess which of the shop's own lines was the slow one - when in
    fact none of them was.
    """
    answer = _ask_within_the_deadline(shopper_id, ask)

    if answer.total_cents is None:
        raise PricingServiceFailed(
            f"{PRICING_HOST} returned no price for "
            f"{BASKET_PATH.format(shopper_id=shopper_id)}"
        )

    return PricedBasket(
        total_cents=answer.total_cents,
        slow_call=_slow_call_words(shopper_id, answer.took_ms)
    )


def _ask_within_the_deadline(shopper_id: str,
                             ask: AskThePricingService) -> PricingAnswer:
    """The service's answer, or `PricingServiceFailed` if it has not come by
    the deadline.

    Whatever the call itself raises is raised here unchanged; only the deadline
    passing is turned into the shop's own words.
    """
    call = _PRICING_CALLS.submit(ask, shopper_id)

    try:
        return call.result(timeout=PRICING_DEADLINE_MS / 1000)
    except FutureTimeout:
        if call.done():
            raise
        call.cancel()
        raise PricingServiceFailed(
            f"{PRICING_HOST} did not answer within {PRICING_DEADLINE_MS}ms for "
            f"{BASKET_PATH.format(shopper_id=shopper_id)}"
        ) from None


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
