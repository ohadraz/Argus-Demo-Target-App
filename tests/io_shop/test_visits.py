from __future__ import annotations

import pytest

from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.payment_provider import AskTheProvider, ProviderAnswer, StoredCard
from io_shop.pricing_service import AskThePricingService, PricingAnswer
from io_shop.visits import (
    HOW_MANY_SHOPPERS_ARE_KEPT,
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
    what_they_saw_last_time,
)

"""What the account page keeps about the shoppers who have been by.

The store itself is small and does what it says. What these pin down is the
part that matters to an incident: it holds one entry per shopper and never more
than its ceiling, so what the process is holding stops growing instead of
climbing with uptime - and that a new process starts empty, which is why
restarting the shop is worth anything.
"""


@pytest.fixture(autouse=True)
def a_shop_that_has_just_started() -> None:
    """Every case begins with an empty store.

    The store is module state, so without this each test would inherit
    whatever the last one left - which is the bug under discussion, and a poor
    thing to also have in the tests about it.
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
    # One entry per shopper, up to the ceiling - which fifty is nowhere near.
    for shopper in range(50):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == 50


def test_a_shopper_coming_back_adds_nothing() -> None:
    for _ in range(50):
        record_visit("shopper-1", "2000")

    assert how_many_shoppers_are_remembered() == 1


def test_the_store_never_grows_past_its_ceiling() -> None:
    # The incident, stated as a bound: a process that keeps meeting shoppers it
    # has never met before holds a fixed amount rather than an amount that
    # climbs with how long it has been up.
    for shopper in range(HOW_MANY_SHOPPERS_ARE_KEPT + 500):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == HOW_MANY_SHOPPERS_ARE_KEPT


def test_the_least_recently_seen_shopper_is_the_one_dropped() -> None:
    # Which entry goes matters: the shoppers still coming back are the ones the
    # panel is for, so room is made from the far end.
    for shopper in range(HOW_MANY_SHOPPERS_ARE_KEPT):
        record_visit(f"shopper-{shopper}", "2000")

    record_visit("shopper-0", "2500")  # seen again, so no longer the oldest
    record_visit("shopper-newcomer", "3000")

    assert how_many_shoppers_are_remembered() == HOW_MANY_SHOPPERS_ARE_KEPT
    assert what_they_saw_last_time("shopper-newcomer") == "3000"
    assert what_they_saw_last_time("shopper-0") == "2500"
    assert what_they_saw_last_time("shopper-1") is None


def test_serving_a_page_records_the_visit() -> None:
    serve_account_page(an_account("shopper-1", 1000, 3000),
                       use_monthly_summary=False,
                       ask_the_provider=a_provider_holding_a_card(),
                       ask_the_pricing_service=a_prompt_pricing_service())

    assert what_they_saw_last_time("shopper-1") == "2000"


def test_a_page_that_failed_is_a_visit_too() -> None:
    # The shopper was here. A store that only grew on success would leave the
    # shop's panel depending on how well it was working, which is not how
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
    # What a new process starts with. The store is bounded now, so a restart is
    # no longer the only thing that brings the heap down - but it still starts
    # from here.
    for shopper in range(20):
        record_visit(f"shopper-{shopper}", "2000")

    forget_every_visit()

    assert how_many_shoppers_are_remembered() == 0
