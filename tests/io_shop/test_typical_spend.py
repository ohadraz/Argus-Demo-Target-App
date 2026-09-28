from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and worked out in one ordering of the
prices rather than one walk of the history per purchase. The last case covers
the second of those: it counts comparisons rather than seconds, so it says
something about the shape of the code rather than about the machine it ran on.
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
    # The middle has to count a repeated price once per purchase rather than
    # once per value - a shopper who bought the same thing five times has a
    # middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


class CountedPrice(int):
    """A price that counts how many times something compared it.

    Comparisons rather than a clock, because what this guards against is the
    shape of the work and not its speed on any particular machine: taking the
    cheapest remaining purchase over and over compares about once per pair of
    purchases, and one ordering compares about once per purchase per doubling.
    """

    comparisons = 0

    def __lt__(self, other: int) -> bool:
        CountedPrice.comparisons += 1

        return int(self) < int(other)


def test_the_middle_is_found_without_re_walking_the_history_per_purchase() -> None:
    # The tail-latency fault: a shopper with a long history was a page that
    # walked that history once per purchase. Two thousand purchases is about a
    # million comparisons that way and about twenty thousand from one ordering.
    how_many = 2_000
    prices = [CountedPrice((index * 7_919) % 100_000) for index in range(how_many)]
    a_long_history = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(int(price) for price in prices),
        total_this_month_cents=0,
    )

    CountedPrice.comparisons = 0
    middle = typical_spend_per_item(a_long_history)

    assert middle == sorted(int(price) for price in prices)[(how_many - 1) // 2]
    assert CountedPrice.comparisons < 20 * how_many
