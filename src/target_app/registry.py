"""The organisation's service registry - what calls what, and whose each one is.

Every company past a certain size keeps one of these, and the thing that is true
of all of them is that nobody reads it until an incident. It is written when a
service is built, it is accurate for about a quarter, and the one fact it holds
that exists nowhere else is which of the things on your request path belong to
somebody you can page and which belong to somebody you can only telephone.

That fact is why this is a registry rather than a field on the shop. A host name
does not carry it - `pricing.io-internal.svc` looks internal because whoever
named it chose that spelling, and a third party running on a private link would
look the same. A team name does not carry it either, because somebody here owns
the *integration* with a third party. It has to be recorded, by a person, and
looked up.

Homegrown rather than shaped like a vendor's catalogue, because that is what most
of these are: a directory somebody keeps, in whatever shape that company decided
on. A stand-in for one particular product would be claiming a fidelity this has
no reason to claim.

The hosts are imported from the shop's own modules rather than written out again,
so the registry cannot come to disagree with the code about where anything is.
That is the one kind of drift a registry must not have: an entry naming an
address nothing dials is worse than no entry, because it is followed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from io_shop.payment_provider import PROVIDER_HOST
from io_shop.pricing_service import PRICING_HOST
from target_app.settings import the_working_cache_endpoint

# What a dependency's ownership can be. Two values today and a string rather than
# a boolean, because a registry that has run for a year has more than two - a
# service another division runs, a vendor a partner resells - and the question a
# reader asks of it is "whose", not "ours or not".
INTERNAL: Final = "internal"
THIRD_PARTY: Final = "third-party"


@dataclass(frozen=True)
class Dependency:
    """One thing a service calls, as the registry records it.

    `purpose` is there because it is what makes the entry worth reading: a
    reader who has just found out that their page calls something wants to know
    what for, and a name alone sends them to the source to find out.

    `owner` is the team or the company, and `ownership` says which of those it
    is. Both, because neither answers the other: knowing a vendor's name does
    not say whether you can page them, and knowing you cannot page them does not
    say who to telephone.
    """

    name: str
    purpose: str
    host: str
    owner: str
    ownership: str


@dataclass(frozen=True)
class RegisteredService:
    """A service as the registry holds it: its name and what it depends on."""

    service: str
    dependencies: tuple[Dependency, ...]


# Which service the shop is registered as. The name the alert uses, because a
# registry keyed by anything else is a registry nobody can look an incident up
# in.
THE_SHOP: Final = "io-shop"


def dependencies_of(service: str) -> RegisteredService:
    """What this service calls, or nothing at all for one nobody registered.

    An unregistered service answers with no dependencies rather than failing.
    That is the honest answer - the registry holds no entry, which is a fact
    about the registry - and it is also the answer that keeps a reader from
    concluding anything: nothing recorded is not the same as nothing called, and
    whoever reads this has to be able to tell those apart from the empty list
    plus the name they asked about.
    """
    if service != THE_SHOP:
        return RegisteredService(service=service, dependencies=())

    return RegisteredService(service=THE_SHOP, dependencies=_WHAT_THE_SHOP_CALLS)


_WHAT_THE_SHOP_CALLS: Final = (
    Dependency(
        name="io-pricing",
        purpose=(
            "What a shopper's basket comes to once their discounts are applied. "
            "Called while the account page renders"
        ),
        host=PRICING_HOST,
        owner="checkout-platform",
        ownership=INTERNAL
    ),
    Dependency(
        name="io-summary-cache",
        purpose=(
            "Holds a shopper's spend figure so the account page does not have "
            "to walk their purchase history. Optional by design - the page is "
            "correct without it"
        ),
        host=the_working_cache_endpoint().host,
        owner="checkout-platform",
        ownership=INTERNAL
    ),
    Dependency(
        name="io-pay",
        purpose=(
            "Holds the card Io charges. Called while the account page renders, "
            "because Io has never stored a card number"
        ),
        host=PROVIDER_HOST,
        owner="IO Payments Ltd",
        ownership=THIRD_PARTY
    )
)
