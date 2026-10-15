from __future__ import annotations

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap enough to be worked out inside
a page render. Both are asserted here: the figure, and the amount of work it
takes to reach it, because a version that rescans the history once per purchase
returns exactly the same figure and only shows up as CPU under load.
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
    # Taking the prices in order has to count a repeated price once per
    # purchase rather than once per value - a shopper who bought the same thing
    # five times has a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_a_history_with_nothing_in_it_has_no_middle() -> None:
    with pytest.raises(ValueError):
        typical_spend_per_item(an_account_of())


class CountedPrice:
    """A price that remembers how often it was compared with another.

    The unit of work for finding a middle is a comparison, so counting them is
    how a test says "once over the history" rather than "once per purchase over
    the history" - which is the difference the account page's CPU bill is made
    of, and which no assertion about the figure itself can see.
    """

    comparisons = 0

    def __init__(self, cents: int) -> None:
        self.cents = cents

    def __lt__(self, other: "CountedPrice") -> bool:
        CountedPrice.comparisons += 1
        return self.cents < other.cents

    def __eq__(self, other: object) -> bool:
        CountedPrice.comparisons += 1
        return isinstance(other, CountedPrice) and self.cents == other.cents

    def __hash__(self) -> int:
        return hash(self.cents)


class CountingPurchase:
    """A purchase whose price counts its own comparisons.

    Duck-typed rather than a `Purchase`, because the figures under test read
    nothing but `price_cents`.
    """

    def __init__(self, price_cents: CountedPrice) -> None:
        self.price_cents = price_cents


def test_the_middle_is_found_without_rescanning_the_history() -> None:
    # Repeatedly taking the cheapest that is left costs a scan per purchase -
    # about n^2/4 comparisons, tens of thousands on the history below - and
    # returns the same figure as ordering the prices once. Under a traffic
    # increase that difference is the account page's CPU limit, so it is
    # asserted rather than left to a load test.
    how_many = 512
    prices = [(index * 7919) % how_many for index in range(how_many)]
    an_account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            CountingPurchase(CountedPrice(price)) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )

    CountedPrice.comparisons = 0
    middle = typical_spend_per_item(an_account)

    assert middle == CountedPrice(sorted(prices)[(how_many - 1) // 2])
    # Ordering once is about n*log2(n) comparisons; a scan per purchase is
    # roughly n^2/4. The bound sits well above the first and far below the
    # second.
    assert CountedPrice.comparisons <= 20 * how_many
