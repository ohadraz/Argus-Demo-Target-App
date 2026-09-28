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
is slow. What it does do is stop waiting. A call that has not answered within
the shop's budget is abandoned and the page renders without a basket total,
because the alternative - waiting however long the other service takes - makes
their slowdown into Io's latency, one render at a time, with nothing here able
to cap it. That is the shape of the 07:52 incident: a dependency that went from
46ms to 1500ms, and a page that dutifully waited out every millisecond.

An abandoned call still says so, in the same words a merely slow one does. The
delay must stay visible in the logs; what must not stay is the waiting.

How the service is reached is the caller's to supply, for the reason the payment
provider's is: the shop is rendered many times over to produce a minute of
telemetry, and a socket per render would be thousands of them per read.
"""

from __future__ import annotations

import threading
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

# How long a call may take before the shop stops waiting for it. Remarking on a
# slow call and waiting out a slow call are different decisions, which is why
# this is a different number: the first is what goes in the logs, the second is
# the most of an account page's time the pricing service is allowed to spend.
# An order of magnitude above what this call normally costs, so that ordinary
# jitter is waited through and an outage-shaped slowdown is not.
PRICING_BUDGET_MS: Final = 500


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

    `total_cents` is `None` only where the service did not answer inside the
    shop's budget and the call was abandoned. That is a page rendered without a
    price rather than a page that failed, and it is the deliberate trade: a
    missing figure on an otherwise correct page, instead of every render in the
    shop waiting on a service that has stopped being quick.
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

    Waits at most `budget_ms` for the service. Inside that, a price is a price:
    it comes back with the words for how slow, so the shop can put them in its
    own logs and a reader can see where a request's time went without having any
    telemetry of the pricing service's own. Beyond it, the call is abandoned and
    the basket comes back without a total - the page is short a figure, and is
    not short the seconds it would have spent waiting.

    Raises `PricingServiceFailed` for an answer that is not a price, and lets
    whatever the seam raised reach the caller unchanged.

    Those words are the whole point of the reporting. A log line saying only
    "account page took 1900ms" describes every slow incident equally, and leaves
    a reader to guess which of the shop's own lines was the slow one - when in
    fact none of them was.
    """
    answer = _answer_within(shopper_id, ask, budget_ms)

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


def _answer_within(shopper_id: str,
                   ask: AskThePricingService,
                   budget_ms: int) -> PricingAnswer | None:
    """The service's answer, or nothing where it did not give one in time.

    The call is made on a thread the shop is willing to walk away from, because
    the seam is somebody's blocking client and there is no other way to put a
    ceiling on a call that will not come back. The abandoned thread is a daemon:
    it finishes or it does not, and either way it holds nothing up, including
    interpreter shutdown.

    Anything the seam raised is re-raised here, on the caller's thread, so that
    a failing pricing service fails exactly as it did before there was a budget.
    """
    if budget_ms is None or budget_ms <= 0:
        return ask(shopper_id)

    outcome: dict[str, object] = {}

    def call() -> None:
        try:
            outcome["answer"] = ask(shopper_id)
        except BaseException as error:  # noqa: BLE001 - carried to the caller
            outcome["error"] = error

    caller = threading.Thread(
        target=call, name=f"pricing-{shopper_id}", daemon=True
    )
    caller.start()
    caller.join(budget_ms / 1000)

    error = outcome.get("error")

    if error is not None:
        raise error  # type: ignore[misc]

    answer = outcome.get("answer")

    if not isinstance(answer, PricingAnswer):
        return None

    return answer


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

    The same three things a slow call names, and the budget in place of the
    duration - because the duration is the one thing the shop deliberately did
    not find out. A reader has to be able to tell this from a page that simply
    had no basket, which is why it says the call was abandoned rather than
    leaving the missing figure to speak for itself.
    """
    return (
        f"{PRICING_HOST} did not answer within {budget_ms}ms for "
        f"{BASKET_PATH.format(shopper_id=shopper_id)} - the call was abandoned "
        f"and the page rendered without a basket total"
    )
