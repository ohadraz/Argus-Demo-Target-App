from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and reached without re-scanning that
history once per purchase. The second half is asserted here as a bound on the
work rather than as a stopwatch: a timing test measures the machine it ran on,
while a count of comparisons measures the shape of the code, which is where
this figure's cost lives.
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
    # Taking the middle of the ordered prices has to count a repeated price
    # once per purchase rather than once per value - a shopper who bought the
    # same thing five times has a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


_COMPARISONS = [0]


class _CountedPrice(int):
    """A price that remembers how often it was compared with another.

    An integer everywhere else, so the figure this produces is the figure a
    shopper would see. The count is the only thing added, and it is what makes
    the cost of finding the middle visible to a test.
    """

    def __lt__(self, other: int) -> bool:
        _COMPARISONS[0] += 1
        return int(self) < int(other)

    def __gt__(self, other: int) -> bool:
        _COMPARISONS[0] += 1
        return int(self) > int(other)

    def __le__(self, other: int) -> bool:
        _COMPARISONS[0] += 1
        return int(self) <= int(other)

    def __ge__(self, other: int) -> bool:
        _COMPARISONS[0] += 1
        return int(self) >= int(other)


def test_the_middle_is_found_without_comparing_every_price_against_every_other() -> None:
    # The incident. Picking the cheapest purchase out over and over scans what
    # is left on every pass, so the work grows with the square of the history -
    # invisible on a short one, which is why p50 never moved, and seconds on the
    # longest ones, which is the whole of the p99 spike. Ordering the prices
    # once costs about n log n, so a bound well under n squared fails against
    # the loop and passes against the sort.
    how_many = 600
    prices = [_CountedPrice((index * 7919) % how_many) for index in range(how_many)]
    a_long_history = Account(
        shopper_id="shopper-with-a-very-long-history",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(int(price) for price in prices),
        total_this_month_cents=0,
    )
    expected = sorted(int(price) for price in prices)[(how_many - 1) // 2]

    _COMPARISONS[0] = 0
    middle = typical_spend_per_item(a_long_history)
    comparisons = _COMPARISONS[0]

    assert middle == expected
    assert comparisons <= how_many * 20
