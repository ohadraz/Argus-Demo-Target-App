from __future__ import annotations

import pytest
from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on every history the shop has:
the figure is one sort, so a history twice as long costs about twice as much.
Lifting the cheapest price out of the list n/2 times reaches the same figure by
rescanning the whole history each time, which is what the long-history case
below refuses to go back to.
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


def test_a_very_long_history_still_has_a_middle_a_request_can_wait_for() -> None:
    # The same shape of fault the lifetime average shipped with, behind the
    # flag rather than on the default path: taking the cheapest that is left
    # twenty thousand times over a forty thousand purchase history is work no
    # page render finishes. One sort answers it.
    a_very_long_history = an_account_of(*range(1, 40_001))

    assert typical_spend_per_item(a_very_long_history) == 20_000


def test_a_shopper_with_no_purchases_has_no_middle() -> None:
    # No figure rather than a made-up one: the page above records the failure
    # and serves a failed response, which is a rate somebody can alert on.
    a_shopper_who_never_bought_anything = Account(
        shopper_id="shopper-with-no-history",
        purchases=(),
        total_cents=0,
        total_this_month_cents=0,
    )

    with pytest.raises(ValueError):
        typical_spend_per_item(a_shopper_who_never_bought_anything)
