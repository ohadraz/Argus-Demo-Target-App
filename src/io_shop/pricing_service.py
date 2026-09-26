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
stop asking a service that has proved it is not answering in time: one call may
wait, but a service that is slow for everybody would otherwise be slow for every
render at once, and a page render holding a worker for a second and a half is
how somebody else's slowdown becomes Io's outage. So a run of calls beyond
`UNACCEPTABLE_CALL_MS` sheds the call for a cooldown - the page comes back
without the basket panel and says so, which costs one panel instead of every
page.

How the service is reached is the caller's to supply, for the reason the payment
provider's is: the shop is rendered many times over to produce a minute of
telemetry, and a socket per render would be thousands of them per read. The
clock is a seam for the same reason a cooldown has to be testable without
waiting out a cooldown.
"""

from __future__ import annotations

import time
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

# How long a call may take before the shop counts it against the service rather
# than merely remarking on it. Well clear of anything a healthy day produces, so
# an ordinary slow afternoon never sheds anything: this is the number that says
# "that call did not arrive in time to be worth having made".
UNACCEPTABLE_CALL_MS: Final = 1000

# How many unacceptable calls in a row before the shop stops asking. More than
# one, because a single slow call is noise and a page that gave up on the first
# one would lose the panel over nothing.
CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING: Final = 3

# How long the shop goes without asking once it has stopped. Long enough that a
# service in trouble is not being asked by every render, short enough that a
# service that recovers is serving panels again within a minute.
SHEDDING_SECONDS: Final = 30.0


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

    `total_cents` is `None` only where the call was not made at all - see
    `shed_call`. A call that was made and answered carries its price.

    `slow_call` is `None` on a call that came back in the time it always does,
    and is a sentence naming the service, the path and the milliseconds when it
    did not. It sits here rather than being raised, because a slow answer is
    still an answer - the same shape as `RenderedPage.cache_failure`, which
    reports something broken underneath a page that rendered perfectly well.

    `shed_call` is the words for a call the shop declined to make, because the
    service has been answering too slowly to be worth waiting for. Also beside
    rather than raised: the page is still served, one panel lighter.
    """

    total_cents: int | None
    slow_call: str | None = None
    shed_call: str | None = None


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

# What time it is, in seconds that only go forwards. A seam so that a cooldown
# can be tested without living through one.
type Clock = Callable[[], float]


class PricingCircuit:
    """What the shop has learned about the pricing service's timekeeping.

    One of these outlives a request, because that is the only place the fact
    lives: no single render can tell a slow call from a slow service, and the
    difference between the two is the whole of this incident. Consecutive calls
    beyond `UNACCEPTABLE_CALL_MS` are what say the service itself is in trouble;
    any acceptable answer says it is not, and clears the count.

    Not thread-safe in the strict sense, and deliberately not made so: the worst
    a racing update can do is shed one call too few or too many, and a lock on
    the request path to protect a counter would cost more than it saves.
    """

    def __init__(self, now: Clock = time.monotonic) -> None:
        self._now = now
        self._consecutive_slow = 0
        self._shedding_until: float | None = None

    def is_shedding(self) -> bool:
        """Whether the shop is currently declining to call the service.

        Asking also ends a cooldown that has run out, and lets exactly one call
        through to find out whether anything has changed. That probe is left one
        strike short of shedding, so a service that is still slow is shed again
        on its own evidence rather than after another full run of slow calls.
        """
        if self._shedding_until is None:
            return False

        if self._now() < self._shedding_until:
            return True

        self._shedding_until = None
        self._consecutive_slow = CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING - 1

        return False

    def record(self, took_ms: int) -> None:
        """What one answered call says about the service.

        Only the duration, because only the duration is this circuit's business.
        A service answering promptly with no price is a different fault, it
        fails its page loudly, and shedding calls to it would hide it.
        """
        if took_ms < UNACCEPTABLE_CALL_MS:
            self._consecutive_slow = 0
            return

        self._consecutive_slow += 1

        if self._consecutive_slow >= CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING:
            self._shedding_until = self._now() + SHEDDING_SECONDS


def basket_total(shopper_id: str,
                 ask: AskThePricingService,
                 circuit: PricingCircuit | None = None) -> PricedBasket:
    """What this shopper's basket comes to, and what the call cost to make.

    Raises `PricingServiceFailed` for anything that is not a price. A slow price
    is a price: it comes back with the words for how slow, so the shop can put
    them in its own logs and a reader can see where a request's time went
    without having any telemetry of the pricing service's own.

    Those words are the whole point of this function. A log line saying only
    "account page took 1900ms" describes every slow incident equally, and leaves
    a reader to guess which of the shop's own lines was the slow one - when in
    fact none of them was.

    Where a `circuit` says the service has been answering too slowly to wait
    for, no call is made at all: the basket comes back without a total and with
    the words for why. Passing no circuit means every call is made, which is the
    behaviour a caller gets who has nowhere to keep what it learned.
    """
    if circuit is not None and circuit.is_shedding():
        return PricedBasket(
            total_cents=None, shed_call=_shed_call_words(shopper_id)
        )

    answer = ask(shopper_id)

    if circuit is not None:
        circuit.record(answer.took_ms)

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


def _shed_call_words(shopper_id: str) -> str:
    """What to say about a call the shop did not make.

    Names the service, the path and the rule that stopped the call, so a reader
    sees that the missing panel is the shop protecting itself from a named
    dependency rather than the shop failing to price a basket.
    """
    return (
        f"not calling {PRICING_HOST} for "
        f"{BASKET_PATH.format(shopper_id=shopper_id)}: "
        f"{CONSECUTIVE_SLOW_CALLS_BEFORE_SHEDDING} consecutive calls took "
        f"{UNACCEPTABLE_CALL_MS}ms or more"
    )
