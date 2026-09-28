from __future__ import annotations

import time

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop has.
The second half matters because this is one of the figures the page works out
for itself when the summary cache is unreachable: if finding the middle rescans
the history once per purchase, a cache that moves to the wrong port becomes a
latency incident while every page it renders stays right.
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
    # Ordering the prices has to count a repeated price once per purchase rather
    # than once per value - a shopper who bought the same thing five times has a
    # middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_the_middle_price_is_found_without_rescanning_the_history_per_purchase(
) -> None:
    # A bound on the work rather than on the answer, because the answer was
    # already right: taking the cheapest remaining purchase over and over scans
    # the whole history once per purchase, and on a history this size that is
    # seconds of a request's time. Putting the prices in order once is
    # milliseconds, so the margin below is about a hundredfold and does not
    # measure the machine it ran on.
    a_very_long_history = an_account_of(
        *((20_000 - index) * 7 % 99_991 for index in range(20_000))
    )

    started = time.perf_counter()
    typical_spend_per_item(a_very_long_history)
    took = time.perf_counter() - started

    assert took < 0.5
