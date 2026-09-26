from __future__ import annotations

import pytest
from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and worked out in one ordering of the
prices rather than by walking the history once per purchase. The long-history
case below is the second of those: it is the shape that turned a rollout going
to every request into a latency incident, and it does not finish in any
reasonable time against an implementation that scans the whole history for
every purchase below the middle.
"""


def an_account_of(*prices: int) -> Account:
    return Account(
        shopper_id="shopper-with-a-history",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )


def test_the_middle_price_of_an_odd_history_is_the_one_in_the_middle() -> None:
    assert typical_spend_per_item(an_account_of(1000, 9000, 3000)) == 3000


def test_an_even_history_takes_the_lower_of_its_two_middles() -> None:
    # A figure interpolated between two prices is one nobody paid, and the
    # whole point of showing the middle rather than the average is that a
    # shopper can point at the purchase it names.
    assert typical_spend_per_item(an_account_of(1000, 3000, 5000, 9000)) == 3000


def test_a_single_purchase_is_its_own_middle() -> None:
    assert typical_spend_per_item(an_account_of(2500)) == 2500


def test_repeated_prices_do_not_lose_the_middle() -> None:
    # Ordering the prices has to count a repeated price once per purchase
    # rather than once per value - a shopper who bought the same thing five
    # times has a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_a_long_history_is_not_walked_once_per_purchase() -> None:
    # The incident this figure caused. Taking the cheapest that is left, over
    # and over, is a full scan of the history for every purchase below the
    # middle - twenty thousand purchases is then two hundred million scanned
    # elements inside a page render, which is the flag going to every request
    # and p99 going with it. Ordering the prices once returns immediately.
    a_long_history = an_account_of(*range(1, 20_001))

    assert typical_spend_per_item(a_long_history) == 10_000


def test_a_shopper_with_no_purchases_has_no_middle() -> None:
    # It raises rather than reporting a made-up figure, and the page above
    # turns that into a failure somebody can alert on.
    with pytest.raises(ValueError):
        typical_spend_per_item(an_account_of())
