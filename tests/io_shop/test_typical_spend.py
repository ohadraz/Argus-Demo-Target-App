from __future__ import annotations

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and found without re-scanning that
history once per purchase. The second half is pinned by counting comparisons
rather than by timing a loop: a test with a stopwatch in it measures the
machine it ran on, where a count of comparisons measures the shape of the code
and is the thing that went wrong.
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


class CountedPrice(int):
    """A price that remembers how often it was compared with another.

    A price is an int and behaves as one everywhere else; the only thing added
    is the tally, because the number of comparisons is exactly the difference
    between ordering the history once and scanning it once per purchase.
    """

    comparisons = 0

    def __lt__(self, other: int) -> bool:
        CountedPrice.comparisons += 1
        return int(self) < int(other)

    def __gt__(self, other: int) -> bool:
        CountedPrice.comparisons += 1
        return int(self) > int(other)


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
    # A repeated price is counted once per purchase rather than once per value -
    # a shopper who bought the same thing five times has a middle, and it is
    # that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_a_history_with_nothing_in_it_has_no_middle() -> None:
    # Raised rather than guessed at, and caught at the request boundary - see
    # `io_shop.account_page`. A middle invented for a shopper who has bought
    # nothing is a figure on the page that nobody paid.
    with pytest.raises(ValueError):
        typical_spend_per_item(an_account_of())


def test_the_middle_is_not_found_by_rescanning_the_history_per_purchase() -> None:
    # The latency regression this figure caused. Taking the cheapest that is
    # left, over and over, compares roughly half the square of the history -
    # for four hundred purchases, tens of thousands of comparisons inside one
    # page render, with the page still perfectly correct on the way out.
    # Ordering the prices once costs a few thousand at that size.
    how_many = 400
    a_long_history = an_account_of(
        *(CountedPrice((index * 7919 % how_many) * 100) for index in range(how_many))
    )
    CountedPrice.comparisons = 0

    middle = typical_spend_per_item(a_long_history)

    assert middle == ((how_many - 1) // 2) * 100
    assert CountedPrice.comparisons <= how_many * 20
