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
is slow. What it does do is say how long it is prepared to wait, because a
caller that waits as long as it takes has handed its own latency to a service
it does not run: a dependency answering in 1500ms makes every page 1500ms, and
no amount of care on this side changes that. So the wait is bounded here. A
call that comes back inside the deadline is a price, slow or not; a call that
does not is abandoned, and the page renders without the figure rather than
holding a shopper for as long as somebody else's incident lasts.

How the service is reached is the caller's to supply, for the reason the payment
provider's is: the shop is rendered many times over to produce a minute of
telemetry, and a socket per render would be thousands of them per read. The
deadline is enforced here rather than left to that seam, because whether a
particular client honours a timeout is not something the shop can see - and the
bound has to hold for every way of reaching the service, not the well-behaved
ones only.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as CallDidNotFinish
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

# How long a call may take before the shop stops waiting for it. Further out
# than the threshold above - the shop remarks on a slow answer long before it
# gives up on one - and far beyond anything this service does when it is well.
# This is the number that caps an account page's time: whatever happens over
# there, a render spends at most this long on the basket.
PRICING_DEADLINE_MS: Final = 500

# Where abandoned calls are left to finish. Shared rather than made per render,
# for the reason the seam itself is handed in: a thread per page would be
# thousands of them per minute of telemetry. A call the shop has given up on
# goes on running here until the service answers, and its answer is dropped.
_CALLS: Final = ThreadPoolExecutor(thread_name_prefix="pricing-call")


@dataclass(frozen=True)
class PricingAnswer:
    """One reply from the pricing service: what the basket comes to, and how
    long the call took.

    The duration is carried on the answer rather than timed by the shop, for the
    reason `ProviderAnswer` carries a status rather than raising on its own: what
    the shop observed about the call is the caller's to report, and turning it
    into the shop's own words is this module's job. It is also what lets a minute
    of telemetry be produced without a minute of waiting.
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

    `total_cents` is `None` where the shop gave up waiting. That is the one case
    in which there is no figure and no failure either: the page is still correct
    about everything it does show, and `slow_call` says what was dropped and
    after how long. A basket figure a shopper waits out an entire incident for
    is worse than a page that arrives without it.
    """

    total_cents: int | None
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


def basket_total(shopper_id: str,
                 ask: AskThePricingService,
                 deadline_ms: int = PRICING_DEADLINE_MS) -> PricedBasket:
    """What this shopper's basket comes to, and what the call cost to make.

    Waits at most `deadline_ms` for the service. Past that the call is left to
    finish on its own and this returns a basket with no total and the words for
    what was abandoned - so a dependency answering in seconds costs the page the
    deadline and not the seconds.

    Raises `PricingServiceFailed` for an answer that is not a price. A slow
    price is still a price: it comes back with the words for how slow, so the
    shop can put them in its own logs and a reader can see where a request's
    time went without having any telemetry of the pricing service's own.

    Those words are the whole point of this function. A log line saying only
    "account page took 1900ms" describes every slow incident equally, and leaves
    a reader to guess which of the shop's own lines was the slow one - when in
    fact none of them was.
    """
    call = _CALLS.submit(ask, shopper_id)

    try:
        answer = call.result(timeout=deadline_ms / 1000)
    except CallDidNotFinish:
        call.cancel()

        return PricedBasket(
            total_cents=None,
            slow_call=_gave_up_words(shopper_id, deadline_ms)
        )

    if answer.total_cents is None:
        raise PricingServiceFailed(
            f"{PRICING_HOST} returned no price for "
            f"{BASKET_PATH.format(shopper_id=shopper_id)}"
        )

    return PricedBasket(
        total_cents=answer.total_cents,
        slow_call=_slow_call_words(shopper_id, answer.took_ms)
    )


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


def _gave_up_words(shopper_id: str, deadline_ms: int) -> str:
    """What to say about a call the shop stopped waiting for.

    The same three things a slow call names - the service, the path, the
    milliseconds - and wording that makes the number the shop's deadline rather
    than the service's answer, because a reader has to be able to tell "it took
    this long" from "we waited this long and left".
    """
    return (
        f"{PRICING_HOST} did not answer within {deadline_ms}ms for "
        f"{BASKET_PATH.format(shopper_id=shopper_id)}"
    )
