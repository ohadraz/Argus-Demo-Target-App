from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap on long ones: the cost is
asserted by counting comparisons rather than timing, so it measures the shape
of the code rather than the machine it ran on.
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


class _CountedPrice(int):
    comparisons = 0

    def __lt__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int.__lt__(self, other)

    def __gt__(self, other: object) -> bool:
        _CountedPrice.comparisons += 1
        return int.__gt__(self, other)


def test_a_long_history_costs_near_linear_comparisons_not_quadratic() -> None:
    # Repeatedly removing the cheapest purchase that is left costs about n^2
    # comparisons - roughly 1.5 million on this history - and that cost is
    # what pushed p99 to seconds once the flag was on. One sort costs n log n.
    n = 2001
    prices = [_CountedPrice((i * 7919) % 100_003) for i in range(n)]
    expected = sorted(int(p) for p in prices)[(n - 1) // 2]

    _CountedPrice.comparisons = 0
    result = typical_spend_per_item(an_account_of(*prices))

    assert result == expected
    assert _CountedPrice.comparisons < 30 * n
