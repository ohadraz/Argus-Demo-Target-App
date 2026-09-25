from __future__ import annotations

from io_shop.payment_provider import PROVIDER_HOST
from io_shop.pricing_service import PRICING_HOST
from target_app.registry import INTERNAL, THE_SHOP, THIRD_PARTY, dependencies_of

"""The registry, and the one fact only it holds.

Everything else about a dependency is discoverable from the shop's own source
and its own logs. Whether the thing on the other end of the call belongs to this
company is not, and getting it wrong is the difference between restarting a
service and telephoning a vendor - so these cases are mostly about that column.
"""


def _named(service: str) -> dict[str, str]:
    return {
        dependency.name: dependency.ownership
        for dependency in dependencies_of(service).dependencies
    }


def test_the_shop_depends_on_the_pricing_service_and_it_is_ours() -> None:
    assert _named(THE_SHOP)["io-pricing"] == INTERNAL


def test_the_payment_provider_is_somebody_elses() -> None:
    assert _named(THE_SHOP)["io-pay"] == THIRD_PARTY


def test_the_registry_holds_more_than_the_two_the_incident_is_about() -> None:
    """A registry with exactly the entries one scenario needs is a prop.

    This one carries the summary cache as well, which no incident here blames
    and which a reader would expect to find.
    """
    assert len(_named(THE_SHOP)) > 2


def test_the_hosts_are_the_ones_the_shop_actually_dials() -> None:
    """The one kind of drift a registry must not have.

    An entry naming an address nothing dials is worse than no entry at all,
    because it is followed. These come from the shop's own modules, and this is
    what says they still do.
    """
    hosts = {
        dependency.name: dependency.host
        for dependency in dependencies_of(THE_SHOP).dependencies
    }

    assert hosts["io-pricing"] == PRICING_HOST
    assert hosts["io-pay"] == PROVIDER_HOST


def test_an_unregistered_service_answers_empty_under_its_own_name() -> None:
    """Nothing recorded is not nothing called.

    The name comes back so a reader can tell which of those two they are
    looking at.
    """
    found = dependencies_of("io-billing")

    assert found.service == "io-billing"
    assert found.dependencies == ()
