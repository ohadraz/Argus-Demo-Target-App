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
part that matters to an incident: it grows with every new shopper *up to a
ceiling* and never past it, which is what stops a worker's heap tracking the
size of Io's audience - and that a new process starts empty, which is why
restarting the shop is worth anything.
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
    # Worth pinning because it is what makes the climb track *traffic* rather
    # than requests: a shop serving the same hundred shoppers all day holds a
    # hundred entries.
    for _ in range(50):
        record_visit("shopper-1", "2000")

    assert how_many_shoppers_are_remembered() == 1


def test_the_store_never_grows_past_its_bound() -> None:
    # The fault, stated plainly: without a ceiling what this holds is a function
    # of how many different shoppers have ever been by, so a process serving the
    # internet holds the internet and its heap climbs while traffic stays flat.
    for shopper in range(MOST_SHOPPERS_REMEMBERED + 500):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == MOST_SHOPPERS_REMEMBERED


def test_the_shoppers_kept_are_the_ones_seen_most_recently() -> None:
    # The bound must be met by dropping whoever has been away longest, not by
    # refusing the shopper who is here now.
    for shopper in range(MOST_SHOPPERS_REMEMBERED + 1):
        record_visit(f"shopper-{shopper}", str(shopper))

    assert what_they_saw_last_time("shopper-0") is None
    assert what_they_saw_last_time(
        f"shopper-{MOST_SHOPPERS_REMEMBERED}"
    ) == str(MOST_SHOPPERS_REMEMBERED)


def test_reading_a_shopper_keeps_them_from_being_evicted() -> None:
    for shopper in range(MOST_SHOPPERS_REMEMBERED):
        record_visit(f"shopper-{shopper}", str(shopper))

    assert what_they_saw_last_time("shopper-0") == "0"

    record_visit("shopper-new", "2000")

    assert what_they_saw_last_time("shopper-0") == "0"
    assert what_they_saw_last_time("shopper-1") is None


def test_serving_a_page_records_the_visit() -> None:
    serve_account_page(an_account("shopper-1", 1000, 3000),
                       use_monthly_summary=False,
                       ask_the_provider=a_provider_holding_a_card(),
                       ask_the_pricing_service=a_prompt_pricing_service())

    assert what_they_saw_last_time("shopper-1") == "2000"


def test_a_page_that_failed_is_a_visit_too() -> None:
    # The shopper was here. A store that only grew on success would leave the
    # shop remembering at a rate that depended on how well it was working,
    # which is not how retained state behaves.
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
    # What a new process starts with, and the whole of what a restart buys.
    for shopper in range(20):
        record_visit(f"shopper-{shopper}", "2000")

    forget_every_visit()

    assert how_many_shoppers_are_remembered() == 0
