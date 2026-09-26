from __future__ import annotations

import pytest

from io_shop import visits
from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.payment_provider import AskTheProvider, ProviderAnswer, StoredCard
from io_shop.pricing_service import AskThePricingService, PricingAnswer
from io_shop.visits import (
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
    what_they_saw_last_time,
)

"""What the account page keeps about the shoppers who have been by.

The store itself is small and does what it says. What these pin down is the
part that matters to an incident: it grows with every new shopper *up to a
bound* and then stops, which is the difference between a cache and a leak - and
that a new process starts empty, which is why restarting the shop is worth
anything.
"""


@pytest.fixture(autouse=True)
def a_shop_that_has_just_started() -> None:
    """Every case begins with an empty store.

    The store is module state, so without this each test would inherit
    whatever the last one left.
    """
    forget_every_visit()


def a_provider_holding_a_card() -> AskTheProvider:
    """The provider answering, which is what it does in every case here - these
    are about what the page remembers, not about who it asks."""
    return lambda dont_care_shopper: ProviderAnswer(
        status=200, card=StoredCard(brand="visa", last_four="4242")
    )


def a_prompt_pricing_service() -> AskThePricingService:
    """The pricing service answering promptly, for the same reason the provider
    above answers at all: these cases are about what the page remembers."""
    return lambda dont_care_shopper: PricingAnswer(total_cents=8400, took_ms=12)


def an_account(shopper_id: str, *prices: int) -> Account:
    return Account(
        shopper_id=shopper_id,
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )


def test_a_shopper_who_has_been_here_before_is_remembered() -> None:
    record_visit("shopper-1", "2000")

    assert what_they_saw_last_time("shopper-1") == "2000"


def test_a_shopper_who_has_never_been_here_is_not() -> None:
    assert what_they_saw_last_time("shopper-nobody") is None


def test_the_newest_visit_is_the_one_kept() -> None:
    record_visit("shopper-1", "2000")
    record_visit("shopper-1", "3000")

    assert what_they_saw_last_time("shopper-1") == "3000"


def test_every_new_shopper_adds_to_what_the_process_is_holding() -> None:
    for shopper in range(50):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == 50


def test_a_shopper_coming_back_adds_nothing() -> None:
    # Worth pinning because it is what makes the store track *shoppers* rather
    # than requests: a shop serving the same hundred shoppers all day holds a
    # hundred entries.
    for _ in range(50):
        record_visit("shopper-1", "2000")

    assert how_many_shoppers_are_remembered() == 1


def test_what_the_process_holds_stops_growing_at_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fault this file is about. Io has millions of registered shoppers, so
    # a store with one entry per shopper ever seen is a heap that climbs all
    # day under flat load and comes down only at a restart. Past the bound the
    # store keeps taking new shoppers and stops taking more memory.
    monkeypatch.setattr(visits, "_HOW_MANY_SHOPPERS_ARE_KEPT", 10)

    for shopper in range(1000):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == 10
    assert what_they_saw_last_time("shopper-999") == "2000"
    assert what_they_saw_last_time("shopper-0") is None


def test_the_shopper_who_keeps_coming_back_is_not_the_one_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What makes the bound cost nothing in practice: the entries given up are
    # the ones nobody has asked for.
    monkeypatch.setattr(visits, "_HOW_MANY_SHOPPERS_ARE_KEPT", 5)

    record_visit("regular", "2000")

    for shopper in range(20):
        record_visit(f"passer-by-{shopper}", "3000")
        assert what_they_saw_last_time("regular") == "2000"

    assert what_they_saw_last_time("regular") == "2000"


def test_serving_a_page_records_the_visit() -> None:
    serve_account_page(an_account("shopper-1", 1000, 3000),
                       use_monthly_summary=False,
                       ask_the_provider=a_provider_holding_a_card(),
                       ask_the_pricing_service=a_prompt_pricing_service())

    assert what_they_saw_last_time("shopper-1") == "2000"


def test_a_page_that_failed_is_a_visit_too() -> None:
    # The shopper was here. A store that only grew on success would behave
    # differently depending on how well the shop was working, which is not how
    # retained state behaves.
    serve_account_page(an_account("shopper-1"),
                       use_monthly_summary=False,
                       ask_the_provider=a_provider_holding_a_card(),
                       ask_the_pricing_service=a_prompt_pricing_service())

    remembered = what_they_saw_last_time("shopper-1")

    assert remembered is not None
    assert remembered.startswith("ZeroDivisionError")


def test_serving_pages_to_many_shoppers_holds_one_entry_each() -> None:
    for shopper in range(20):
        serve_account_page(
            an_account(f"shopper-{shopper}", 1000, 3000),
            use_monthly_summary=False,
            ask_the_provider=a_provider_holding_a_card(),
            ask_the_pricing_service=a_prompt_pricing_service()
        )

    assert how_many_shoppers_are_remembered() == 20


def test_a_restarted_shop_is_holding_nothing() -> None:
    # What a new process starts with, and what a restart buys.
    for shopper in range(20):
        record_visit(f"shopper-{shopper}", "2000")

    forget_every_visit()

    assert how_many_shoppers_are_remembered() == 0
