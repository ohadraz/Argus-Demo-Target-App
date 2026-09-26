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

The shop does not retry and does not price the basket itself. What it does do
is stop asking. A call that comes back slowly is still an answer and is used;
but a service that has answered too slowly several times running is a service
the shop leaves alone for a while, because the alternative is that every account
page render pays that service's latency for as long as it lasts. That is how a
slowdown over there became a slowdown here: one request waiting is somebody
else's problem, every request waiting is ours.

While the shop is leaving the service alone the page renders without the basket
figure, the way it renders without a cache hit - the missing figure is said out
loud in the same place a slow call is, so a reader sees it in the logs rather
than in the error rate.

How the service is reached is the caller's to supply, for the reason the payment
provider's is: the shop is rendered many times over to produce a minute of
telemetry, and a socket per render would be thousands of them per read.
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

# How long a call may take before the shop stops being willing to wait for the
# next one. Deliberately far above `SLOW_CALL_MS`: remarking on a call and
# giving up on a service are different decisions, and a dependency that merely
# sits near the logging threshold must never be shed for it.
TOO_SLOW_MS: Final = 1000

# How many bad calls in a row - too slow, or no price at all - before the shop
# stops calling. More than one, because a single slow answer is weather; few
# enough that a service in the state io-pricing was in stops costing every
# render its latency within a handful of requests.
BAD_CALLS_BEFORE_PAUSING: Final = 3

# How long the shop leaves the service alone before letting one request through
# to find out whether it has recovered. One request, not all of them: a service
# coming back to a thundering herd is a service that goes away again.
PAUSE_SECONDS: Final = 30.0


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

    `total_cents` is `None` on a render where the shop did not call the service
    at all because it is currently too slow to be worth waiting for. The page
    shows one fewer figure and is otherwise itself, which is the same bargain
    the summary cache's fallback makes.

    `slow_call` is `None` on a call that came back in the time it always does,
    and is a sentence naming the service, the path and the milliseconds when it
    did not - or naming the service and the pause when the shop skipped it. It
    sits here rather than being raised, because a slow answer is still an
    answer - the same shape as `RenderedPage.cache_failure`, which reports
    something broken underneath a page that rendered perfectly well.
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

# What time it is, for the pause. A seam so that a test can state the passage of
# thirty seconds without spending them.
type Clock = Callable[[], float]


class PricingServiceHealth:
    """What the shop has lately observed about the pricing service.

    Small and deliberately blunt: how many calls in a row went badly, and until
    when the shop has decided not to call again. It is the whole of the shop's
    protection against a dependency that has gone slow, and it lives in the
    client because that is the only side of the wire Io controls.
    """

    def __init__(self, now: Clock = time.monotonic) -> None:
        self._now = now
        self._bad_calls_in_a_row = 0
        self._paused_until: float | None = None

    def is_paused(self) -> bool:
        """Whether the shop is currently leaving the service alone.

        Asking clears an expired pause, which is what lets exactly one request
        through to find out whether the service has recovered: until that
        request reports back, the count still stands, so a service that is still
        slow is paused again by its own next answer.
        """
        if self._paused_until is None:
            return False

        if self._now() >= self._paused_until:
            self._paused_until = None
            return False

        return True

    def seconds_left(self) -> float:
        """How much of the pause is still to run, for the words that say so."""
        if self._paused_until is None:
            return 0.0

        return max(0.0, self._paused_until - self._now())

    def bad_calls_in_a_row(self) -> int:
        return self._bad_calls_in_a_row

    def went_well(self) -> None:
        """A call the shop is happy to have made. The service is forgiven."""
        self._bad_calls_in_a_row = 0
        self._paused_until = None

    def went_badly(self) -> None:
        """A call that took too long or brought back no price."""
        self._bad_calls_in_a_row += 1

        if self._bad_calls_in_a_row >= BAD_CALLS_BEFORE_PAUSING:
            self._paused_until = self._now() + PAUSE_SECONDS


# What the shop remembers about the service across requests, for the callers
# that do not keep a record of their own - which is every caller in the shop.
_HEALTH = PricingServiceHealth()


def forget_pricing_health() -> None:
    """Start again as a freshly started process does, holding no opinion about
    the pricing service. For tests, and for the same reason `visits` has one.
    """
    global _HEALTH

    _HEALTH = PricingServiceHealth()


def basket_total(shopper_id: str,
                 ask: AskThePricingService,
                 health: PricingServiceHealth | None = None) -> PricedBasket:
    """What this shopper's basket comes to, and what the call cost to make.

    Raises `PricingServiceFailed` for anything that is not a price. A slow price
    is a price: it comes back with the words for how slow, so the shop can put
    them in its own logs and a reader can see where a request's time went
    without having any telemetry of the pricing service's own.

    Those words are the whole point of the reporting. A log line saying only
    "account page took 1900ms" describes every slow incident equally, and leaves
    a reader to guess which of the shop's own lines was the slow one - when in
    fact none of them was.

    Where the service has been too slow too many times running, no call is made
    at all: this returns a basket with no total and the words for the pause,
    and the render costs nothing. That is the difference between one slow
    request and a slow shop.

    `health` is what the shop has observed about the service. It defaults to the
    record the process keeps, and is an argument so that a caller - or a test -
    can hold its own.
    """
    health = _HEALTH if health is None else health

    if health.is_paused():
        return PricedBasket(
            total_cents=None,
            slow_call=_pause_words(shopper_id, health)
        )

    answer = ask(shopper_id)

    if answer.total_cents is None:
        health.went_badly()

        raise PricingServiceFailed(
            f"{PRICING_HOST} returned no price for "
            f"{BASKET_PATH.format(shopper_id=shopper_id)}"
        )

    if answer.took_ms >= TOO_SLOW_MS:
        health.went_badly()
    else:
        health.went_well()

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


def _pause_words(shopper_id: str, health: PricingServiceHealth) -> str:
    """What to say about a call the shop deliberately did not make.

    Names the service and the path, like a slow call does, and then says why
    and for how long - because a figure missing from a page is a thing a reader
    has to be able to account for, and "the shop chose not to ask" is an
    entirely different fact from "the service said nothing".
    """
    return (
        f"not calling {PRICING_HOST} for "
        f"{BASKET_PATH.format(shopper_id=shopper_id)}: "
        f"{health.bad_calls_in_a_row()} slow calls in a row, "
        f"trying again in {health.seconds_left():.0f}s"
    )
