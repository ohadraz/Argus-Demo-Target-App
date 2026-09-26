"""The card Io keeps on file for a shopper, which Io does not keep.

The account page shows the card a shopper will be charged with, and the shop has
never held that number: it lives with the payment provider, and the page asks
for it while it renders. That makes the provider part of every account page -
another company's service on Io's request path, which is the ordinary shape of a
shop and the reason an outage somewhere else becomes an outage here.

Nothing in this module retries and nothing invents a card. What it does do is
stop asking: a provider that has failed the last `FAILURES_BEFORE_GIVING_UP`
calls in a row is not asked again for `COOLDOWN_SECONDS`, and every request in
that window fails at once rather than waiting out somebody else's timeout. That
is not softening the failure - the failure is reported in the same words either
way - it is refusing to spend Io's latency budget on a service that has just
said, repeatedly, that it has nothing to give. How the provider is reached is
the caller's to supply, so this is the only lever the shop has over what a dead
provider costs it.

What the shop does with that failure is the boundary's decision, not this
module's: see `io_shop.account_page`, which renders the rest of the page and
reports the missing card beside it.

How the provider is reached is the caller's to supply. The shop states what it
asks for and what it does with the answer, and whoever is running it says where
that answer comes from.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final


# Who Io banks with. A host rather than a product name, because a host is what
# appears in a failed request's own words - and those words are what somebody
# reading the logs at three in the morning has to recognise as not-ours.
PROVIDER_HOST = "api.io-pay.example"

# What is asked for. Spelled out rather than built, so the path in a failure
# line is the path the shop actually requested.
CARD_PATH = "/v1/shoppers/{shopper_id}/card"

# The only answer that carries a card.
_OK = 200

# How many failures in a row mean the provider is down rather than this shopper
# being unlucky. Small enough that an outage stops costing the shop latency
# within a second or two of traffic, large enough that a single rejected shopper
# never stops the shop asking on anyone else's behalf.
FAILURES_BEFORE_GIVING_UP: Final = 5

# How long the shop goes without asking, once it has given up. One request after
# this is spent finding out whether the provider is back, which is the cheapest
# possible way to notice a recovery Io will not otherwise be told about.
COOLDOWN_SECONDS: Final = 30.0


@dataclass(frozen=True)
class StoredCard:
    """A card as the provider describes it: enough to show, never enough to
    charge with.

    The provider holds the number; what it hands back is what a page may
    display. A shop that received more than this would be storing card details
    it went to a provider precisely to avoid storing.
    """

    brand: str
    last_four: str


@dataclass(frozen=True)
class ProviderAnswer:
    """One reply from the provider: the status it answered with, and the card if
    it had one.

    The status is carried rather than interpreted by whoever fetched it, because
    turning a provider's status into the shop's own failure is this module's
    job - and a fetcher that raised on its own would put that decision in as
    many places as there are ways to reach the provider.
    """

    status: int
    card: StoredCard | None = None


class PaymentProviderFailed(Exception):
    """The provider did not answer with a card.

    Named for what the shop can see. Io has no way to tell a provider that is
    down from one that is refusing this particular shopper, and inventing the
    distinction in an exception type would be the shop claiming knowledge of
    another company's internals.
    """


# How the provider is reached, given a shopper. A seam rather than a request,
# because the shop is rendered many times over to produce a minute of telemetry
# and a socket per render would be thousands of them per read.
type AskTheProvider = Callable[[str], ProviderAnswer]


# What the shop has lately seen the provider do. Process state, like the visit
# store: a new process starts willing to ask, which is all a restart buys and
# all it needs to.
_failures_in_a_row = 0
_gave_up_at: float | None = None


def forget_provider_health() -> None:
    """Start asking again as though nothing had happened.

    For a process that wants to begin clean - and for tests, which would
    otherwise inherit whatever the previous case taught the shop about the
    provider.
    """
    global _failures_in_a_row, _gave_up_at

    _failures_in_a_row = 0
    _gave_up_at = None


def card_on_file(shopper_id: str, ask: AskTheProvider) -> StoredCard:
    """The card the provider holds for this shopper.

    Raises `PaymentProviderFailed` for anything that is not a card, and the
    message is the point of the function: it names the provider's host, the
    path Io asked for, and the status that came back. A failure that said only
    "could not load card" would be indistinguishable, in a log, from a bug in
    the shop.

    Raises it without asking at all while the shop has given up on the provider,
    and says so in those words. A request that waits out a timeout it already
    knows the answer to is latency the shop is choosing to spend.
    """
    if _given_up_on_the_provider():
        raise PaymentProviderFailed(
            f"{PROVIDER_HOST} was not asked for "
            f"{CARD_PATH.format(shopper_id=shopper_id)}: the last "
            f"{FAILURES_BEFORE_GIVING_UP} calls to it failed"
        )

    try:
        answer = ask(shopper_id)
    except Exception:
        _that_went_badly()
        raise

    if answer.status != _OK or answer.card is None:
        _that_went_badly()

        raise PaymentProviderFailed(
            f"{PROVIDER_HOST} returned {answer.status} for "
            f"{CARD_PATH.format(shopper_id=shopper_id)}"
        )

    _that_went_fine()

    return answer.card


def _given_up_on_the_provider() -> bool:
    """Whether the shop is currently not asking, and still within the window it
    decided that in.

    The window closing does not mean the provider is well - nothing here can
    know that - it means the next request is the one that finds out.
    """
    if _gave_up_at is None:
        return False

    return time.monotonic() - _gave_up_at < COOLDOWN_SECONDS


def _that_went_badly() -> None:
    """Remember one more failure, and give up once there have been enough in a
    row."""
    global _failures_in_a_row, _gave_up_at

    _failures_in_a_row += 1

    if _failures_in_a_row >= FAILURES_BEFORE_GIVING_UP:
        _gave_up_at = time.monotonic()


def _that_went_fine() -> None:
    """Forget the run of failures. One card is proof enough that the provider is
    answering, which is the only question the count was ever asking."""
    global _failures_in_a_row, _gave_up_at

    _failures_in_a_row = 0
    _gave_up_at = None
