from __future__ import annotations

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and reached without re-walking that
history once per purchase. The last of those is asserted here as a bound on the
work done rather than on the clock: a timing test measures the machine it ran
on, while a count of comparisons measures the shape of the code, which is the
thing that put this figure's rollout into an incident.
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
    # A repeated price has to count once per purchase rather than once per
    # value - a shopper who bought the same thing five times has a middle, and
    # it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_a_shopper_with_no_history_has_no_middle() -> None:
    # Reported rather than invented: the boundary above turns this into a failed
    # response, and a made-up figure would be a wrong number instead.
    with pytest.raises(ValueError):
        typical_spend_per_item(an_account_of())


class _CountingPrice(int):
    """A price that counts how often it is compared with another price.

    The cost of this figure is comparisons of prices, so counting them is the
    machine-independent way to say how much work finding the middle took. Only
    the ordering comparisons are counted; equality is what a list's `remove`
    uses, and counting it would measure a different thing.
    """

    comparisons = 0

    @classmethod
    def start_counting(cls) -> None:
        cls.comparisons = 0

    def __lt__(self, other: object) -> bool:
        _CountingPrice.comparisons += 1
        return int(self) < int(other)  # type: ignore[call-overload]

    def __gt__(self, other: object) -> bool:
        _CountingPrice.comparisons += 1
        return int(self) > int(other)  # type: ignore[call-overload]

    def __le__(self, other: object) -> bool:
        _CountingPrice.comparisons += 1
        return int(self) <= int(other)  # type: ignore[call-overload]

    def __ge__(self, other: object) -> bool:
        _CountingPrice.comparisons += 1
        return int(self) >= int(other)  # type: ignore[call-overload]


# A history long enough to tell the two shapes apart, and the number of
# comparisons that separates them. Ordering the history once costs roughly
# n log n - about twenty-two thousand here. Re-scanning what is left of it once
# per purchase costs well over a million, which is the tail latency the flag
# exposed.
A_LONG_HISTORY = 2001
WORK_A_SINGLE_ORDERING_TAKES = 100_000


def test_the_middle_is_found_without_rescanning_the_history_once_per_purchase() -> None:
    # The fault behind the latency incident: the figure was right and the work
    # grew with the square of the history, so only the shoppers with long
    # histories were slow - p99 moved and nothing else did. A bound on the work
    # rather than on the clock, because a correct-but-quadratic implementation
    # passes any test that only checks the answer.
    prices = [(index * 7_919) % 250_000 for index in range(A_LONG_HISTORY)]
    a_shopper_with_a_long_history = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(price_cents=_CountingPrice(price), in_current_month=False)
            for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )

    _CountingPrice.start_counting()
    middle = typical_spend_per_item(a_shopper_with_a_long_history)

    assert middle == sorted(prices)[(A_LONG_HISTORY - 1) // 2]
    assert _CountingPrice.comparisons < WORK_A_SINGLE_ORDERING_TAKES
