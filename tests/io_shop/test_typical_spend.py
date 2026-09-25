from __future__ import annotations

import pytest
from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop has.
The cost is asserted by counting price comparisons rather than by timing a loop,
because a timing test measures the machine it ran on; the count follows the
shape of the code, which is the thing that made account pages slow.
"""


class CountedPrice(int):
    """A price that records every time it is compared with another.

    An `int` so that it behaves exactly like a price everywhere else, with the
    comparisons the search performs counted on the way past.
    """

    comparisons = 0

    def __lt__(self, other: object) -> bool:
        CountedPrice.comparisons += 1
        return int(self) < int(other)  # type: ignore[call-overload]

    def __gt__(self, other: object) -> bool:
        CountedPrice.comparisons += 1
        return int(self) > int(other)  # type: ignore[call-overload]


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
    # The history holds one price per purchase, so a repeated price counts once
    # per purchase rather than once per value - a shopper who bought the same
    # thing five times has a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_an_account_that_has_bought_nothing_has_no_middle() -> None:
    # There is no middle of nothing. It reaches the request boundary as a
    # failure rather than becoming a number nobody paid.
    with pytest.raises(ValueError):
        typical_spend_per_item(
            Account(shopper_id="shopper-with-no-history", purchases=(),
                    total_cents=0, total_this_month_cents=0)
        )


def test_the_middle_is_not_found_by_rescanning_the_history() -> None:
    # The incident: finding the middle by taking the cheapest that is left, over
    # and over, scans the whole history once per purchase below the middle -
    # around n^2/4 comparisons, which on a real shopper's history is the
    # difference between a page that renders in milliseconds and one that does
    # not. Ordering the history once costs on the order of n log n.
    history = 512
    prices = [(i * 7) % history for i in range(history)]
    account = an_account_of(*prices)
    quadratic = (history * history) // 4

    CountedPrice.comparisons = 0
    counted = Account(
        shopper_id=account.shopper_id,
        purchases=tuple(
            Purchase(price_cents=CountedPrice(purchase.price_cents),
                     in_current_month=False)
            for purchase in account.purchases
        ),
        total_cents=account.total_cents,
        total_this_month_cents=0,
    )

    assert typical_spend_per_item(counted) == sorted(prices)[(history - 1) // 2]
    assert CountedPrice.comparisons < quadratic // 4
