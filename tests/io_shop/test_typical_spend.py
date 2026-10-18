from __future__ import annotations

import time

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and - since the rollout that put it on
every page sent p99 to seconds - cheap on a long one too.
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


def test_an_empty_history_still_fails_rather_than_inventing_a_middle() -> None:
    with pytest.raises(ValueError):
        typical_spend_per_item(an_account_of())


def test_a_long_history_does_not_cost_quadratic_time() -> None:
    # The incident: removing the cheapest purchase over and over is a full scan
    # per removal, so a 50,000-purchase history meant ~25,000 scans of up to
    # 50,000 prices - tens of seconds. Sorting once takes milliseconds; a
    # second is a generous bound that only the quadratic shape can miss.
    prices = [(index * 7919) % 100_003 for index in range(50_000)]
    a_long_history = an_account_of(*prices)

    started = time.perf_counter()
    figure = typical_spend_per_item(a_long_history)
    took = time.perf_counter() - started

    assert figure == sorted(prices)[(len(prices) - 1) // 2]
    assert took < 1.0
