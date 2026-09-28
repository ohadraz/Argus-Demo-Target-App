"""What the account page's figures cost to work out, not just what they say.

The figures were always right; what took the shop down was how much work each
render did. Both of the figures below were quadratic in the length of a
shopper's history - one re-summed every prefix of the purchases, the other
removed the cheapest price over and over - so the CPU a render burned grew with
the square of the history and the service could not fit its traffic into the
capacity it had.

So these tests assert the work rather than the answer. A test that only checked
the figure passed against the quadratic code, which is precisely how this
reached production. The work is counted rather than timed: a stopwatch measures
the machine the suite ran on, whereas the number of integer additions and
comparisons a function performs is a property of the code.
"""

from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.spend_summary import average_spend_per_item
from io_shop.typical_spend import typical_spend_per_item


class CountingCents(int):
    """A price that keeps a tally of the arithmetic done to it.

    An `int` in every way that matters to the code under test - the figures
    come out identical - which is what makes it a measurement rather than a
    different scenario.
    """

    additions = 0
    comparisons = 0

    @classmethod
    def start_counting(cls) -> None:
        cls.additions = 0
        cls.comparisons = 0

    def __add__(self, other: int) -> CountingCents:  # type: ignore[override]
        CountingCents.additions += 1
        return CountingCents(int(self) + int(other))

    __radd__ = __add__  # type: ignore[assignment]

    def __lt__(self, other: int) -> bool:
        CountingCents.comparisons += 1
        return int(self) < int(other)

    def __gt__(self, other: int) -> bool:
        CountingCents.comparisons += 1
        return int(self) > int(other)


HOW_LONG_A_HISTORY = 512


def a_long_history() -> Account:
    """A history of the size a long-standing shopper really has.

    The prices are shuffled rather than ascending so that neither figure is
    helped by an order it would not meet in the shop.
    """
    prices = [
        CountingCents(((index * 37) % HOW_LONG_A_HISTORY) * 100 + 99)
        for index in range(HOW_LONG_A_HISTORY)
    ]

    return Account(
        shopper_id="shopper-of-long-standing",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(int(price) for price in prices),
        total_this_month_cents=0,
    )


def test_the_lifetime_average_walks_the_history_once() -> None:
    # It re-summed every prefix: a 512-purchase history cost ~131,000 additions
    # to reach a total that one pass reaches in 512.
    account = a_long_history()
    CountingCents.start_counting()

    average_spend_per_item(account)

    assert CountingCents.additions <= 4 * HOW_LONG_A_HISTORY


def test_the_typical_purchase_orders_the_history_once() -> None:
    # It took the cheapest remaining price over and over: ~100,000 comparisons
    # on the same history, against the ~4,600 of a single ordering.
    account = a_long_history()
    CountingCents.start_counting()

    typical_spend_per_item(account)

    assert CountingCents.comparisons <= 20 * HOW_LONG_A_HISTORY


def test_both_figures_are_the_ones_they_always_were() -> None:
    # The cost is what changed; the figures must not have. A history of
    # 100, 200, ... 900 averages to 500 and has 500 in the middle of it.
    prices = [index * 100 for index in range(1, 10)]
    account = Account(
        shopper_id="shopper-with-a-tidy-history",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )

    assert average_spend_per_item(account) == 500
    assert typical_spend_per_item(account) == 500
