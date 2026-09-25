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
is slow. What it does do is stop waiting. A call on a render path with no
deadline is the dependency's latency wearing the shop's name: every page waits
as long as pricing takes, so a service that slows to a second and a half makes
every account page a second and a half slower, for as long as it lasts. So the
call gets a budget. Inside it, a slow answer is still an answer and the shop
says how long it waited; past it, the call is abandoned and the page renders
without the basket figure - the same bargain the summary cache strikes, where
something underneath is broken and the page is still served.

How the service is reached is the caller's to supply, for the reason the payment
provider's is: the shop is rendered many times over to produce a minute of
telemetry, and a socket per render would be thousands of them per read. Because
the seam is an ordinary blocking call, the only way to put a deadline on it from
this side is to wait on it somewhere else - so it runs on a small pool of worker
threads, and abandoning it means abandoning the wait rather than the socket.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as CallTookTooLong
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

# How long a call may take before the shop stops waiting for it. Well above what
# pricing has ever needed and well below the point at which the page's own
# latency is somebody else's number: the budget exists so that a dependency's
# bad minute costs the shop this much and no more, whatever the dependency does.
PRICING_BUDGET_MS: Final = 500

# How many pricing calls may be in flight at once. A bulkhead rather than a
# tuning knob: when pricing is wedged, the calls already waiting hold these
# threads and every later render gives up on its budget instead of adding a
# thread to a queue that is going nowhere.
_MAX_CALLS_IN_FLIGHT: Final = 32

_CALLS: Final = ThreadPoolExecutor(max_workers=_MAX_CALLS_IN_FLIGHT,
                                   thread_name_prefix="pricing-call")


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

    `total_cents` is `None` where the call was abandoned for taking longer than
    the shop's budget. That is the one case where the shop has no figure and no
    failure either: the page is still correct about everything else it shows,
    and `slow_call` carries the words for the figure that is missing.
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
                 budget_ms: int = PRICING_BUDGET_MS) -> PricedBasket:
    """What this shopper's basket comes to, and what the call cost to make.

    Waits at most `budget_ms` for the service. A price that arrives inside the
    budget is returned, slow or not, with the words for how slow - so the shop
    can put them in its own logs and a reader can see where a request's time
    went without having any telemetry of the pricing service's own.

    A call that does not answer inside the budget is abandoned: this returns a
    basket with no total and the words for the call that was given up on, and
    the page renders without the figure. That is deliberate. The alternative is
    the one the shop used to take - wait however long pricing takes - and it
    makes every account page as slow as the slowest dependency, which is how a
    pricing slowdown becomes an Io latency incident.

    Raises `PricingServiceFailed` where the service answered without a price,
    and lets anything the seam itself raises through: neither is something this
    module can decide about on the page's behalf.

    Those words are the whole point of the reporting. A log line saying only
    "account page took 1900ms" describes every slow incident equally, and leaves
    a reader to guess which of the shop's own lines was the slow one - when in
    fact none of them was.
    """
    answer = _ask_within_budget(shopper_id, ask, budget_ms)

    if answer is None:
        return PricedBasket(
            total_cents=None,
            slow_call=_abandoned_call_words(shopper_id, budget_ms)
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


def _ask_within_budget(shopper_id: str,
                       ask: AskThePricingService,
                       budget_ms: int) -> PricingAnswer | None:
    """The service's answer, or nothing where it did not arrive in time.

    The seam blocks, so the deadline has to be imposed from outside it: the call
    is handed to a worker and this waits on the worker rather than on the
    socket. Giving up cancels the call if it has not started yet, so a queue
    that built up while pricing was slow does not go on hammering it afterwards.

    Anything the seam raises is re-raised here, unchanged and on this thread, so
    a broken pricing client still reaches the request boundary as itself.
    """
    call = _CALLS.submit(ask, shopper_id)

    try:
        return call.result(timeout=budget_ms / 1000)
    except CallTookTooLong:
        call.cancel()
        return None


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


def _abandoned_call_words(shopper_id: str, budget_ms: int) -> str:
    """What to say about a call the shop stopped waiting for.

    Says the budget rather than a duration, because the duration is the one
    thing the shop deliberately never found out - and says that the page went
    out without the figure, so a reader is not left wondering whether the
    shopper saw a wrong number. They saw no number.
    """
    return (
        f"{PRICING_HOST} did not answer within {budget_ms}ms for "
        f"{BASKET_PATH.format(shopper_id=shopper_id)} - "
        f"page rendered without a basket total"
    )
