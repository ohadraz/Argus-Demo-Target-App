from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and - since the rollout that put a
second of latency on every page it reached - cheap on every history the shop
has. The last test here is the one that pins the cost: it counts how many times
two prices are compared, rather than timing a loop, so it measures the shape of
the code and not the machine it ran on.
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
    # Taking the cheapest that is left, over and over, has to count a repeated
    # price once per purchase rather than once per value - a shopper who bought
    # the same thing five times has a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


class _CountedPrice(int):
    """An amount in pence that remembers being compared with another.

    An int, so the figure that comes back out of the module is the figure a
    shopper would be shown. The count is what the test is actually about: the
    answer was right before this fix too, and only the work changed.
    """

    comparisons = 0

    def __lt__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int(self) < int(other)  # type: ignore[call-overload]

    def __gt__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int(self) > int(other)  # type: ignore[call-overload]


def test_the_middle_is_found_without_rescanning_the_history_for_every_purchase() -> None:
    # The latency the rollout exposed. Taking the cheapest that is left, over
    # and over, compares every remaining price once per removal - about 1.5
    # million comparisons on the history below - and that is what took the
    # account page's p99 from 200ms to over 1700ms the moment the flag reached
    # every request. Ordering the history once costs about 22,000.
    how_many = 2_000
    prices = [_CountedPrice((index * 37) % how_many) for index in range(how_many)]
    a_long_history = Account(
        shopper_id="shopper-who-has-been-here-for-years",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(int(price) for price in prices),
        total_this_month_cents=0,
    )

    _CountedPrice.comparisons = 0
    middle = typical_spend_per_item(a_long_history)
    spent_comparing = _CountedPrice.comparisons

    # (index * 37) % 2000 walks every price from 0 to 1999 exactly once, so the
    # lower of the two middles is 999.
    assert middle == 999
    assert spent_comparing < 40 * how_many
