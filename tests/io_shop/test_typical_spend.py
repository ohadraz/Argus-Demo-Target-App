from __future__ import annotations

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on all of them too. The second
is covered here rather than left to the generator: this figure sits on the path
every request takes when the summary cache cannot be reached, so a cost that
grows with the square of a history is an outage-shaped latency jump waiting for
the next cache incident. What is asserted is the number of comparisons the code
performs, not the seconds it takes - that measures the shape of the code rather
than the machine it ran on.
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
    # Ordering the history has to count a repeated price once per purchase
    # rather than once per value - a shopper who bought the same thing five
    # times has a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_a_history_with_nothing_in_it_says_so_rather_than_inventing_a_middle() -> None:
    a_shopper_who_never_bought_anything = Account(
        shopper_id="shopper-with-no-history",
        purchases=(),
        total_cents=0,
        total_this_month_cents=0,
    )

    with pytest.raises(ValueError):
        typical_spend_per_item(a_shopper_who_never_bought_anything)


class _CountedPrice(int):
    """A price that tallies every comparison it takes part in.

    Comparisons rather than seconds: the question is how the work grows with
    the history, and a stopwatch over a few hundred integers answers a question
    about the machine instead.
    """

    comparisons = 0

    def __lt__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int(self) < int(other)  # type: ignore[call-overload]

    def __gt__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int(self) > int(other)  # type: ignore[call-overload]

    def __le__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int(self) <= int(other)  # type: ignore[call-overload]

    def __ge__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int(self) >= int(other)  # type: ignore[call-overload]

    def __eq__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int(self) == int(other)  # type: ignore[call-overload]

    __hash__ = int.__hash__


def test_the_middle_is_found_without_a_scan_per_purchase() -> None:
    # The regression the cache was hiding. Taking the cheapest that is left,
    # over and over, walks the whole remaining history once per purchase below
    # the middle - on this history that is around a hundred thousand
    # comparisons, and it is what turned a cache outage into a p50 of 190ms.
    # Ordering the history once costs a few thousand.
    how_many = 512
    a_shuffled_history = [
        _CountedPrice(((index * 37) % how_many) * 100) for index in range(how_many)
    ]

    account = an_account_of(*a_shuffled_history)
    _CountedPrice.comparisons = 0

    found = typical_spend_per_item(account)

    assert found == 255 * 100
    assert _CountedPrice.comparisons < how_many * 20
