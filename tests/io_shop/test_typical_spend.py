from __future__ import annotations

import random

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correctness on every history the shop has, and a bound on how much comparing
it does: the cost is counted in comparisons rather than timed, so the test
measures the shape of the code rather than the machine it runs on.
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


def test_an_empty_history_raises_value_error() -> None:
    with pytest.raises(ValueError):
        typical_spend_per_item(an_account_of())


class _CountingPrice(int):
    """An int that counts how often it is compared."""

    comparisons = 0

    def __lt__(self, other: object) -> bool:
        _CountingPrice.comparisons += 1
        return int.__lt__(self, other)

    def __gt__(self, other: object) -> bool:
        _CountingPrice.comparisons += 1
        return int.__gt__(self, other)

    def __eq__(self, other: object) -> bool:
        _CountingPrice.comparisons += 1
        return int.__eq__(self, other)

    __hash__ = int.__hash__


def test_a_long_history_costs_n_log_n_comparisons_not_n_squared() -> None:
    # Repeatedly taking the cheapest that is left costs about n*n/2
    # comparisons; one sort costs about n*log2(n). At n=2000 that is roughly
    # two million against roughly twenty-two thousand - the bound sits
    # comfortably between them.
    n = 2000
    rng = random.Random(1103)
    raw = [rng.randrange(100, 200_000) for _ in range(n)]
    prices = [_CountingPrice(price) for price in raw]
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(raw),
        total_this_month_cents=0,
    )

    _CountingPrice.comparisons = 0
    middle = typical_spend_per_item(account)
    comparisons = _CountingPrice.comparisons

    assert int(middle) == sorted(raw)[(n - 1) // 2]
    assert comparisons < 30 * n, comparisons
