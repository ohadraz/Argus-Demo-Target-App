from __future__ import annotations

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop
has. The second half matters because this runs on the page's fallback path:
whenever the summary cache cannot be reached, every request works the figure
out here, and a version that rescanned the history once per purchase made a
lost cache into a shop-wide slowdown. What that costs is measured below in
comparisons rather than in seconds - a stopwatch would measure the machine the
test ran on.
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
    # Putting the prices in order has to count a repeated price once per
    # purchase rather than once per value - a shopper who bought the same thing
    # five times has a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_a_shopper_with_no_history_has_no_middle() -> None:
    # Nothing is invented for them. The account page's boundary turns this into
    # a reported failure, which is what taking the cheapest of nothing did too.
    with pytest.raises(ValueError):
        typical_spend_per_item(
            Account(
                shopper_id="shopper-with-no-history",
                purchases=(),
                total_cents=0,
                total_this_month_cents=0,
            )
        )


class CountedPrice(int):
    """A price that counts every comparison made against it.

    An int, so everything treats it as the money it is, with the comparisons
    the shop makes while finding the middle counted on the way past.
    """

    comparisons = 0

    def __lt__(self, other: object) -> bool:
        CountedPrice.comparisons += 1
        return int(self) < int(other)  # type: ignore[call-overload]

    def __gt__(self, other: object) -> bool:
        CountedPrice.comparisons += 1
        return int(self) > int(other)  # type: ignore[call-overload]

    def __eq__(self, other: object) -> bool:
        CountedPrice.comparisons += 1
        return int(self) == other

    def __hash__(self) -> int:
        return int.__hash__(self)


def test_the_middle_is_found_without_rescanning_the_history_once_per_purchase() -> None:
    # Taking the cheapest that is left, over and over, walked the remaining
    # prices twice per purchase - tens of thousands of comparisons on the
    # history below - and that cost was invisible while the summary cache was
    # answering. Ordering the prices once needs about n log n, comfortably
    # inside the bound here, and a quadratic version cannot get near it.
    how_many = 400
    prices = [
        CountedPrice((index * 7919) % how_many * 100) for index in range(how_many)
    ]
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(int(price) for price in prices),
        total_this_month_cents=0,
    )
    CountedPrice.comparisons = 0

    figure = typical_spend_per_item(account)

    assert figure == sorted(int(price) for price in prices)[(how_many - 1) // 2]
    assert CountedPrice.comparisons <= 25 * how_many
