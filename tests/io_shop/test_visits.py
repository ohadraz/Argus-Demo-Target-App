from __future__ import annotations

import pytest

from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.payment_provider import AskTheProvider, ProviderAnswer, StoredCard
from io_shop.visits import (
    MOST_SHOPPERS_REMEMBERED,
    forget_every_visit,
    how_many_shoppers_are_remembered,
    record_visit,
    what_they_saw_last_time,
)

"""What the account page keeps about the shoppers who have been by.

The store itself is small and does what it says. What these pin down is the
part that matters to an incident: that it is *bounded*, so a process serving
millions of different shoppers over days holds a fixed amount rather than one
entry per shopper who ever visited - and that what it drops when it is full is
the shoppers longest unseen, so the panel still works for the ones who come
back. The unbounded version of this store is what made the heap a function of
uptime while traffic stayed flat.
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


def test_a_few_shoppers_are_all_held() -> None:
    # Under the bound nothing is dropped: the ordinary case is still one entry
    # per shopper who has been by.
    for shopper in range(50):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == 50


def test_a_shopper_coming_back_adds_nothing() -> None:
    for _ in range(50):
        record_visit("shopper-1", "2000")

    assert how_many_shoppers_are_remembered() == 1


def test_more_shoppers_than_the_cap_does_not_grow_the_store() -> None:
    # The leak, inverted. Before this was bounded, what the process held was a
    # function of how many different shoppers had been by and nothing ever took
    # any of it away - so a worker that stayed up for days grew until the heap
    # limit found it, with traffic perfectly flat the whole time. Three times
    # the cap goes through here and the store does not move past the cap.
    for shopper in range(MOST_SHOPPERS_REMEMBERED * 3):
        record_visit(f"shopper-{shopper}", "2000")

    assert how_many_shoppers_are_remembered() == MOST_SHOPPERS_REMEMBERED


def test_the_store_never_passes_the_cap_while_it_fills() -> None:
    # Not just at the end. A store that overshot and then trimmed would still
    # have allocated the overshoot, which is the thing being prevented.
    for shopper in range(MOST_SHOPPERS_REMEMBERED + 500):
        record_visit(f"shopper-{shopper}", "2000")

        assert how_many_shoppers_are_remembered() <= MOST_SHOPPERS_REMEMBERED


def test_the_shoppers_dropped_are_the_ones_longest_unseen() -> None:
    # An eviction policy rather than an arbitrary cull: the oldest goes, the
    # newest stays. A store that dropped the most recent entries would hold the
    # bound and be useless, because the shoppers it kept would be the ones least
    # likely to come back.
    for shopper in range(MOST_SHOPPERS_REMEMBERED + 1):
        record_visit(f"shopper-{shopper}", "2000")

    assert what_they_saw_last_time("shopper-0") is None
    assert what_they_saw_last_time(f"shopper-{MOST_SHOPPERS_REMEMBERED}") == "2000"


def test_a_shopper_who_keeps_coming_back_is_not_dropped() -> None:
    # What makes the bound acceptable. A regular is refreshed by every visit, so
    # the panel keeps working for the people it was built for while the
    # one-time visitors age out.
    record_visit("shopper-regular", "2000")

    for shopper in range(MOST_SHOPPERS_REMEMBERED * 2):
        record_visit(f"shopper-{shopper}", "2000")
        record_visit("shopper-regular", "2000")

    assert what_they_saw_last_time("shopper-regular") == "2000"
    assert how_many_shoppers_are_remembered() == MOST_SHOPPERS_REMEMBERED


def test_reading_a_visit_keeps_that_shopper_held() -> None:
    # Reading is a visit too - it is the page showing the panel. A store that
    # aged entries only by when they were written would evict a shopper the
    # moment before serving them the thing it evicted.
    record_visit("shopper-read", "2000")

    for shopper in range(MOST_SHOPPERS_REMEMBERED - 1):
        record_visit(f"shopper-{shopper}", "2000")
        assert what_they_saw_last_time("shopper-read") == "2000"

    record_visit("shopper-last", "2000")

    assert what_they_saw_last_time("shopper-read") == "2000"


def test_serving_a_page_records_the_visit() -> None:
    serve_account_page(an_account("shopper-1", 1000, 3000),
                       use_monthly_summary=False,
                       ask_the_provider=a_provider_holding_a_card())

    assert what_they_saw_last_time("shopper-1") == "2000"


def test_a_page_that_failed_is_a_visit_too() -> None:
    # The shopper was here. A store that only grew on success would leave the
    # shop's retained state depending on how well it was working, which is not
    # how retained state behaves.
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


def test_serving_pages_past_the_cap_does_not_grow_the_store() -> None:
    # The same bound through the path that actually writes to the store, since
    # that is where the growth came from in production: one entry per rendered
    # page, for as long as the process lived.
    for shopper in range(MOST_SHOPPERS_REMEMBERED + 200):
        serve_account_page(
            an_account(f"shopper-{shopper}", 1000, 3000),
            use_monthly_summary=False,
            ask_the_provider=a_provider_holding_a_card()
        )

    assert how_many_shoppers_are_remembered() == MOST_SHOPPERS_REMEMBERED


def test_a_restarted_shop_is_holding_nothing() -> None:
    for shopper in range(20):
        record_visit(f"shopper-{shopper}", "2000")

    forget_every_visit()

    assert how_many_shoppers_are_remembered() == 0
