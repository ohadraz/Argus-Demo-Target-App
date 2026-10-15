from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop has.
The first is most of what this file covers; the last case covers the second,
because a figure that costs a page more the longer a history gets is what turns
a busy hour into a core that is saturated whatever it is given.
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
    # Finding the middle has to count a repeated price once per purchase rather
    # than once per value - a shopper who bought the same thing five times has
    # a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


class _PriceThatCountsComparisons(int):
    """A price that says how often it was compared with another.

    How the cost of this figure is measured without timing anything: the work
    is comparisons between prices, so counting them measures the shape of the
    code rather than the machine the test ran on.
    """

    comparisons = 0

    def __lt__(self, other: int) -> bool:  # type: ignore[override]
        type(self).comparisons += 1
        return int(self) < int(other)

    def __gt__(self, other: int) -> bool:  # type: ignore[override]
        type(self).comparisons += 1
        return int(self) > int(other)


def test_a_long_history_is_not_searched_once_per_purchase() -> None:
    # The incident. Taking the cheapest that is left, over and over, costs
    # about n*n/4 comparisons - upwards of a million on the history below - so
    # the CPU one page needs grows with the square of the history behind it,
    # and added capacity is eaten as fast as it arrives. Ordering the prices
    # once costs about n*log2(n), some twenty thousand here.
    how_many = 2000
    prices = [(index * 7919) % how_many for index in range(how_many)]
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(
                price_cents=_PriceThatCountsComparisons(price),
                in_current_month=False,
            )
            for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )
    _PriceThatCountsComparisons.comparisons = 0

    middle = typical_spend_per_item(account)

    assert middle == 999
    assert _PriceThatCountsComparisons.comparisons <= 100_000
