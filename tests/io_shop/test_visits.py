from __future__ import annotations

import pytest

from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.payment_provider import AskTheProvider, ProviderAnswer, StoredCard
from io_shop.visits import (
    HOW_MANY_SHOPPERS_ARE_KEPT,
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
    what_they_saw_last_time,
)

"""What the account page keeps about the shoppers who have been by.

The store itself is small and does what it says. What these pin down is the
part that matters to an incident: it holds the shoppers who have been by most
recently and no more than that, so what the process retains is a fixed cost
rather than a function of how long it has been running - and that a new process
starts empty.
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
    for _ in range(50):
        record_visit("shopper-1", "2000")

    assert how_many_shoppers_are_remembered() == 1


def test_the_store_never_holds_more_than_it_keeps() -> None:
    # The fault, and the fix for it: a shop that serves the internet used to
    # hold the internet. Now the internet costs it a ceiling.
    for shopper in range(HOW_MANY_SHOPPERS_ARE_KEPT + 500):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == HOW_MANY_SHOPPERS_ARE_KEPT


def test_the_shopper_dropped_is_the_one_longest_unseen() -> None:
    for shopper in range(HOW_MANY_SHOPPERS_ARE_KEPT):
        record_visit(f"shopper-{shopper}", "2000")

    record_visit("shopper-newest", "3000")

    assert what_they_saw_last_time("shopper-0") is None
    assert what_they_saw_last_time("shopper-newest") == "3000"
    assert what_they_saw_last_time(f"shopper-{HOW_MANY_SHOPPERS_ARE_KEPT - 1}") == "2000"


def test_a_shopper_who_keeps_coming_back_is_kept() -> None:
    record_visit("shopper-regular", "2000")

    for shopper in range(HOW_MANY_SHOPPERS_ARE_KEPT - 1):
        record_visit(f"shopper-{shopper}", "2000")
        # Being looked up counts as being seen, which is what keeps a shopper
        # the shop is actually serving out of the way of eviction.
        assert what_they_saw_last_time("shopper-regular") == "2000"

    record_visit("shopper-newest", "3000")

    assert what_they_saw_last_time("shopper-regular") == "2000"


def test_an_evicted_shopper_is_simply_a_shopper_we_do_not_know() -> None:
    # Eviction costs a shopper exactly what never having visited costs them:
    # the page still renders, because the figure is worked out either way.
    for shopper in range(HOW_MANY_SHOPPERS_ARE_KEPT + 1):
        record_visit(f"shopper-{shopper}", "2000")

    page = serve_account_page(an_account("shopper-0", 1000, 3000),
                              use_monthly_summary=False,
                              ask_the_provider=a_provider_holding_a_card())

    assert page.failure is None
    assert page.figure_cents == 2000


def test_serving_a_page_records_the_visit() -> None:
    serve_account_page(an_account("shopper-1", 1000, 3000),
                       use_monthly_summary=False,
                       ask_the_provider=a_provider_holding_a_card())

    assert what_they_saw_last_time("shopper-1") == "2000"


def test_a_page_that_failed_is_a_visit_too() -> None:
    # The shopper was here. A store that only grew on success would behave
    # differently depending on how well the shop was working, which is not how
    # retained state behaves.
    serve_account_page(an_account("shopper-1"),
                       use_monthly_summary=False,
                       ask_the_provider=a_provider_holding_a_card())

    remembered = what_they_saw_last_time("shopper-1")

    assert remembered is not None
    assert remembered.startswith("ZeroDivisionError")


def test_serving_pages_to_many_shoppers_holds_one_entry_each() -> None:
    for shopper in range(20):
        serve_account_page(
            an_account(f"shopper-{shopper}", 1000, 3000),
            use_monthly_summary=False,
            ask_the_provider=a_provider_holding_a_card()
        )

    assert how_many_shoppers_are_remembered() == 20


def test_a_restarted_shop_is_holding_nothing() -> None:
    for shopper in range(20):
        record_visit(f"shopper-{shopper}", "2000")

    forget_every_visit()

    assert how_many_shoppers_are_remembered() == 0
