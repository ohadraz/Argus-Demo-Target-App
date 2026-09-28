from __future__ import annotations

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop has.
The second is asserted here by counting comparisons rather than by timing
anything: a stopwatch measures the machine the suite ran on, where the number
of comparisons one render costs is a property of the code.
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


class CountingPrice(int):
    """A price that counts every comparison anybody makes against it.

    A price is an integer and behaves as one; all this adds is the tally, which
    is what says whether the middle was found by ordering the history once or
    by scanning it once per purchase.
    """

    comparisons = 0

    def __lt__(self, other: object) -> bool:
        CountingPrice.comparisons += 1
        return int(self) < int(other)  # type: ignore[call-overload]

    def __gt__(self, other: object) -> bool:
        CountingPrice.comparisons += 1
        return int(self) > int(other)  # type: ignore[call-overload]

    def __le__(self, other: object) -> bool:
        CountingPrice.comparisons += 1
        return int(self) <= int(other)  # type: ignore[call-overload]

    def __ge__(self, other: object) -> bool:
        CountingPrice.comparisons += 1
        return int(self) >= int(other)  # type: ignore[call-overload]

    def __eq__(self, other: object) -> bool:
        CountingPrice.comparisons += 1
        return int(self) == other

    __hash__ = int.__hash__


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
    # Taking the middle of the ordered history has to count a repeated price
    # once per purchase rather than once per value - a shopper who bought the
    # same thing five times has a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_a_history_with_no_purchases_has_no_middle() -> None:
    a_shopper_who_never_bought_anything = Account(
        shopper_id="shopper-with-no-history", purchases=(), total_cents=0,
        total_this_month_cents=0
    )

    with pytest.raises(ValueError):
        typical_spend_per_item(a_shopper_who_never_bought_anything)


def test_the_middle_is_found_without_comparing_every_price_against_every_other(
) -> None:
    # The cost of this figure is paid on every render of the busiest page the
    # shop has. Repeatedly taking the cheapest that is left scans the whole
    # history once per purchase removed - quadratic - which is invisible until
    # traffic quadruples and then is three cores pinned at their limit.
    how_many = 256
    scrambled = [
        CountingPrice(((index * 97) % how_many) * 100) for index in range(how_many)
    ]
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False)
            for price in scrambled
        ),
        total_cents=0,
        total_this_month_cents=0,
    )

    CountingPrice.comparisons = 0
    middle = typical_spend_per_item(account)
    comparisons = CountingPrice.comparisons

    assert int(middle) == ((how_many - 1) // 2) * 100
    assert comparisons <= 20 * how_many, (
        f"{comparisons} comparisons for {how_many} purchases - the middle is "
        "being found by scanning the history once per purchase"
    )
