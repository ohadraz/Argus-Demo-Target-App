from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop has.
The last case covers the cost rather than the answer: the figure was right
before too, and what made it an incident was how much CPU a render spent
getting there.
"""


_comparisons = 0


class _CountedPrice(int):
    """A price that counts every comparison it takes part in.

    An int in every other respect, so the figure it produces is the figure a
    real price would. Counting comparisons rather than seconds means the case
    measures the shape of the code instead of the machine the suite ran on.
    """

    def __lt__(self, other: int) -> bool:  # type: ignore[override]
        global _comparisons
        _comparisons += 1
        return int.__lt__(self, other)

    def __gt__(self, other: int) -> bool:  # type: ignore[override]
        global _comparisons
        _comparisons += 1
        return int.__gt__(self, other)


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
    # Taking the middle has to count a repeated price once per purchase rather
    # than once per value - a shopper who bought the same thing five times has
    # a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_the_middle_costs_one_ordering_pass_not_one_per_purchase() -> None:
    # The figure was always right; what it cost was the incident. Rescanning
    # what is left for every purchase below the middle is quadratic - about
    # 1.5 million comparisons for the history below - and that CPU is spent on
    # every render of the most-visited page the shop has.
    global _comparisons

    a_long_history = [(index * 7919) % 4001 for index in range(2000)]
    the_middle = sorted(a_long_history)[(len(a_long_history) - 1) // 2]
    account = an_account_of(*(_CountedPrice(price) for price in a_long_history))

    _comparisons = 0
    figure = typical_spend_per_item(account)

    assert figure == the_middle
    # One ordering pass is n log n - about 22,000 here. Generous room above
    # that, and far below what rescanning costs.
    assert _comparisons <= 30 * len(a_long_history)
