"""The card Io keeps on file for a shopper, which Io does not keep.

The account page shows the card a shopper will be charged with, and the shop has
never held that number: it lives with the payment provider, and the page asks
for it while it renders. That makes the provider part of every account page -
another company's service on Io's request path, which is the ordinary shape of a
shop and the reason an outage somewhere else becomes an outage here.

Nothing in this module retries, falls back, or renders the page without the
card. That is deliberate rather than unfinished: when the provider is down there
is nothing the shop can do about it, and code that softened the failure would
turn an incident about somebody else's outage into an incident about Io's
resilience.

How the provider is reached is the caller's to supply. The shop states what it
asks for and what it does with the answer, and whoever is running it says where
that answer comes from.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


# Who Io banks with. A host rather than a product name, because a host is what
# appears in a failed request's own words - and those words are what somebody
# reading the logs at three in the morning has to recognise as not-ours.
PROVIDER_HOST = "api.io-pay.example"

# What is asked for. Spelled out rather than built, so the path in a failure
# line is the path the shop actually requested.
CARD_PATH = "/v1/shoppers/{shopper_id}/card"

# The only answer that carries a card.
_OK = 200


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


def card_on_file(shopper_id: str, ask: AskTheProvider) -> StoredCard:
    """The card the provider holds for this shopper.

    Raises `PaymentProviderFailed` for anything that is not a card, and the
    message is the point of the function: it names the provider's host, the
    path Io asked for, and the status that came back. A failure that said only
    "could not load card" would be indistinguishable, in a log, from a bug in
    the shop.
    """
    answer = ask(shopper_id)

    if answer.status != _OK or answer.card is None:
        raise PaymentProviderFailed(
            f"{PROVIDER_HOST} returned {answer.status} for "
            f"{CARD_PATH.format(shopper_id=shopper_id)}"
        )

    return answer.card
