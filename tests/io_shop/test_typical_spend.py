from __future__ import annotations

import time

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop has.
The last case is about the second of those: the figure is rendered inside a
request, so a history that costs a scan per purchase is latency a shopper waits
through.
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
    # A repeated price counts once per purchase rather than once per value - a
    # shopper who bought the same thing five times has a middle, and it is that
    # thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_the_middle_of_a_long_history_does_not_cost_the_square_of_it() -> None:
    # The figure is rendered inside a request, so its cost is the shopper's
    # wait. Taking the cheapest that is left, over and over, walks the
    # remaining history once per step: on a long history that is on the order
    # of a billion elementary operations and seconds of wall clock. Ordering
    # the prices once is milliseconds, and the bound below is far enough above
    # it to survive a slow machine while still failing the scan-per-purchase
    # shape by a wide margin.
    prices = [(index * 7919) % 100_000 for index in range(40_000)]
    a_very_long_history = an_account_of(*prices)

    started = time.perf_counter()
    middle = typical_spend_per_item(a_very_long_history)
    took = time.perf_counter() - started

    assert middle == sorted(prices)[(len(prices) - 1) // 2]
    assert took < 2.0
