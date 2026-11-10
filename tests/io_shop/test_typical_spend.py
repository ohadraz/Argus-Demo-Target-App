from __future__ import annotations

import random

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and bounded in how much comparing it
does to get there: the bound is asserted as a count of comparisons rather than
a time, so it measures the shape of the code and not the machine it ran on.
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
    # A repeated price has to count once per purchase rather than once per
    # value - a shopper who bought the same thing five times has a middle, and
    # it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_a_history_with_no_purchases_has_no_middle() -> None:
    with pytest.raises(ValueError):
        typical_spend_per_item(an_account_of())


_COMPARISONS = [0]


class _CountedPrice(int):
    """A price that counts how often it is compared for ordering."""

    def __lt__(self, other: object) -> bool:
        _COMPARISONS[0] += 1
        return int.__lt__(self, other)  # type: ignore[operator]

    def __gt__(self, other: object) -> bool:
        _COMPARISONS[0] += 1
        return int.__gt__(self, other)  # type: ignore[operator]


def test_the_middle_of_a_long_history_takes_far_fewer_than_quadratic_comparisons() -> None:
    # Taking the cheapest that is left, over and over, compares prices several
    # hundred thousand times on a thousand purchases; one sort needs on the
    # order of n log n, about ten thousand.
    count = 1000
    prices = list(range(1, count + 1))
    random.Random(0).shuffle(prices)
    long_history = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(price_cents=_CountedPrice(price), in_current_month=False)
            for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )

    _COMPARISONS[0] = 0
    middle = typical_spend_per_item(long_history)

    assert middle == (count + 1) // 2
    assert _COMPARISONS[0] < 50_000
