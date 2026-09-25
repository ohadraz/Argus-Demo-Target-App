from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop has,
which is the whole of what this file covers. Cheapness is counted rather than
timed: this figure is what a request falls back to when the cache cannot be
reached, so every request can be computing it at once, and a test timing a loop
would measure the machine it ran on rather than the shape of the code.
"""


class _CountedPrice(int):
    """A price that tallies every comparison it takes part in.

    Finding a middle is comparisons and nothing else, so the tally is a faithful
    count of the work - and the same number on every machine.
    """

    comparisons = 0

    @classmethod
    def reset(cls) -> None:
        cls.comparisons = 0

    def _counted(self) -> None:
        type(self).comparisons += 1

    def __lt__(self, other: int) -> bool:
        self._counted()
        return int(self) < int(other)

    def __gt__(self, other: int) -> bool:
        self._counted()
        return int(self) > int(other)

    def __eq__(self, other: object) -> bool:
        self._counted()
        return int(self) == other

    __hash__ = int.__hash__


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


def test_the_middle_costs_one_ordering_not_a_pass_per_purchase_below_it() -> None:
    # The cache-miss path. Taking the cheapest that is left, over and over,
    # walks what remains once per purchase below the middle - work that grows
    # with the square of the history, unnoticed while the cache is answering
    # and the page's whole latency the minute it stops.
    how_many = 256
    prices = [(index * 37) % how_many for index in range(how_many)]
    a_long_history = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(price_cents=_CountedPrice(price), in_current_month=False)
            for price in prices
        ),
        total_cents=0,
        total_this_month_cents=0,
    )

    _CountedPrice.reset()
    figure = typical_spend_per_item(a_long_history)

    assert figure == sorted(prices)[(how_many - 1) // 2]
    # Ordering once is about n log n comparisons (~2,000 here); a pass per
    # purchase below the middle would be tens of thousands.
    assert _CountedPrice.comparisons <= 20 * how_many
