from __future__ import annotations

import pytest

from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.payment_provider import AskTheProvider, ProviderAnswer, StoredCard
from io_shop.pricing_service import AskThePricingService, PricingAnswer
from io_shop.visits import (
    MOST_SHOPPERS_REMEMBERED,
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
    what_they_saw_last_time,
)

"""What the account page keeps about the shoppers who have been by.

The store itself is small and does what it says. What these pin down is the
part that matters to an incident: it grows with every new shopper but only up
to a limit, past which the least recently seen shopper is dropped - which is
what keeps a flat-traffic shop from a heap that climbs all day - and that a new
process starts empty.
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


def test_the_store_stops_growing_at_its_limit() -> None:
    # The fault this store had, stated as the thing that must not happen again:
    # a shop meeting an endless supply of new shoppers on flat traffic held one
    # entry for each of them forever, and the heap climbed until a restart.
    for shopper in range(MOST_SHOPPERS_REMEMBERED + 500):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == MOST_SHOPPERS_REMEMBERED


def test_the_oldest_shopper_is_the_one_dropped() -> None:
    # And which entry goes: the one rendered longest ago. A shopper who was
    # dropped is a shopper who gets the empty panel once, which is what a
    # first-time shopper gets anyway.
    for shopper in range(MOST_SHOPPERS_REMEMBERED + 1):
        record_visit(f"shopper-{shopper}", "2000")

    assert what_they_saw_last_time("shopper-0") is None
    assert what_they_saw_last_time("shopper-1") == "2000"
    assert what_they_saw_last_time(
        f"shopper-{MOST_SHOPPERS_REMEMBERED}"
    ) == "2000"


def test_a_shopper_who_keeps_coming_back_is_not_dropped() -> None:
    # What makes the limit safe: the eviction is by how recently a shopper was
    # rendered, so the regulars stay and the passers-by are what falls off.
    record_visit("shopper-regular", "2000")

    for shopper in range(MOST_SHOPPERS_REMEMBERED):
        record_visit(f"shopper-{shopper}", "2000")
        record_visit("shopper-regular", "3000")

    assert what_they_saw_last_time("shopper-regular") == "3000"
    assert how_many_shoppers_are_remembered() == MOST_SHOPPERS_REMEMBERED


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
    # What a new process starts with. No longer the only cure for the climb,
    # but still what a fresh process has.
    for shopper in range(20):
        record_visit(f"shopper-{shopper}", "2000")

    forget_every_visit()

    assert how_many_shoppers_are_remembered() == 0
