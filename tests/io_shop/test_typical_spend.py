from __future__ import annotations

import time

import pytest
from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, which is most of what this file covers.
What it costs is covered too, and deliberately: the figure was correct on the
day it made p99 jump from 200ms to nearly two seconds, so correctness alone was
never going to say whether the rollout is safe to leave on.
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


def test_a_history_with_nothing_in_it_has_no_middle() -> None:
    # Raising rather than reporting a zero nobody spent. The page above turns
    # this into a recorded failure; a made-up figure would be a number on the
    # account page that nothing in the shop can account for.
    a_shopper_who_never_bought_anything = Account(
        shopper_id="shopper-with-no-history",
        purchases=(),
        total_cents=0,
        total_this_month_cents=0,
    )

    with pytest.raises(ValueError):
        typical_spend_per_item(a_shopper_who_never_bought_anything)


def test_a_long_history_does_not_cost_the_square_of_its_length() -> None:
    # The incident, as a test. The figure was always correct - what the rollout
    # exposed at a hundred percent was its cost: taking the cheapest that is
    # left, over and over, walks the remaining history twice per step and so
    # costs the square of its length. On the history below that is around two
    # hundred million list walks, tens of seconds of CPU on the request path of
    # the shop's most-visited page, and it is what put p99 at 1900ms with the
    # error rate flat.
    #
    # The budget is deliberately loose - an ordering of this history is a few
    # milliseconds on any machine, and anything quadratic misses a second by
    # orders of magnitude. What is being asserted is the shape of the cost, not
    # the speed of the runner.
    prices = [(index * 7_919) % 50_000 for index in range(20_000)]
    a_long_history = an_account_of(*prices)

    started = time.perf_counter()
    middle = typical_spend_per_item(a_long_history)
    took = time.perf_counter() - started

    assert middle == sorted(prices)[(len(prices) - 1) // 2]
    assert took < 2.0
