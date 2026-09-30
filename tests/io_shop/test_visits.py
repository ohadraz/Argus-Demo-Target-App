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
part that matters to an incident: it grows with every new shopper, it stops
growing at a ceiling the deployment sets rather than at one traffic sets, and a
new process starts empty.
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
    # Below the ceiling the store is what it always was: one entry per shopper
    # who has been by.
    for shopper in range(50):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == 50


def test_a_shopper_coming_back_adds_nothing() -> None:
    for _ in range(50):
        record_visit("shopper-1", "2000")

    assert how_many_shoppers_are_remembered() == 1


def test_the_store_never_holds_more_than_it_is_allowed_to() -> None:
    # The fault this pins: what the process holds has to be bounded by the
    # ceiling and not by how many different shoppers have been by. Without one,
    # a replica's footprint tracks its uptime and the process dies holding the
    # reporting nobody noticed was coming from it.
    for shopper in range(MOST_SHOPPERS_REMEMBERED + 500):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == MOST_SHOPPERS_REMEMBERED


def test_the_shopper_dropped_is_the_one_seen_longest_ago() -> None:
    # Recency, because the only thing this store is worth anything for is a
    # shopper who comes back - and a shopper who has been dropped reads exactly
    # as one who has never been here, which every caller already handles.
    for shopper in range(MOST_SHOPPERS_REMEMBERED):
        record_visit(f"shopper-{shopper}", "2000")

    record_visit("shopper-newest", "3000")

    assert what_they_saw_last_time("shopper-0") is None
    assert what_they_saw_last_time("shopper-newest") == "3000"
    assert (
        what_they_saw_last_time(f"shopper-{MOST_SHOPPERS_REMEMBERED - 1}") == "2000"
    )


def test_a_returning_shopper_is_not_the_one_dropped() -> None:
    # Coming back puts a shopper at the recent end, so the store keeps the
    # shoppers who are actually using it rather than the ones who arrived first.
    record_visit("shopper-0", "2000")

    for shopper in range(1, MOST_SHOPPERS_REMEMBERED):
        record_visit(f"shopper-{shopper}", "2000")

    record_visit("shopper-0", "4000")
    record_visit("shopper-newest", "3000")

    assert what_they_saw_last_time("shopper-0") == "4000"
    assert what_they_saw_last_time("shopper-1") is None


def test_serving_a_page_records_the_visit() -> None:
    serve_account_page(an_account("shopper-1", 1000, 3000),
                       use_monthly_summary=False,
                       ask_the_provider=a_provider_holding_a_card(),
                       ask_the_pricing_service=a_prompt_pricing_service())

    assert what_they_saw_last_time("shopper-1") == "2000"


def test_a_page_that_failed_is_a_visit_too() -> None:
    # The shopper was here. A store that only grew on success would leave the
    # shop's memory tracking how well it was working, which is not how retained
    # state behaves.
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
