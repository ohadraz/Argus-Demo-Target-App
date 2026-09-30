from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.typical_spend import typical_spend_per_item

"""The middle of a shopper's history - the account page's newest figure.

Correct on every history the shop has, and cheap enough to be asked for on
every render of the shop's most-visited page. The last of those is asserted
here by counting the comparisons the figure costs rather than by timing it: a
clock measures the machine the test ran on, and what went wrong was the shape
of the code - work that grew with the square of a shopper's history.
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


class CountedPrice(int):
    """A price that says how often it was compared against another.

    An `int` in every other respect, so the figure it produces is the figure a
    real history produces.
    """

    comparisons = 0

    def __lt__(self, other: object) -> bool:
        CountedPrice.comparisons += 1
        return int(self) < int(other)  # type: ignore[call-overload]

    def __gt__(self, other: object) -> bool:
        CountedPrice.comparisons += 1
        return int(self) > int(other)  # type: ignore[call-overload]


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
    # Taking the middle has to count a repeated price once per purchase rather
    # than once per value - a shopper who bought the same thing five times has
    # a middle, and it is that thing.
    assert typical_spend_per_item(an_account_of(500, 500, 500, 9000, 9000)) == 500


def test_one_expensive_buy_does_not_drag_the_middle_the_way_it_drags_a_mean() -> None:
    # Why the figure exists at all. Twenty coffees and a laptop average to a
    # price describing neither; the middle still describes the coffee.
    a_history_of_coffees_and_a_laptop = an_account_of(*([300] * 20), 180_000)

    assert typical_spend_per_item(a_history_of_coffees_and_a_laptop) == 300


def test_the_middle_is_found_without_walking_the_history_once_per_purchase() -> None:
    """What the incident was: the cost grew with the square of the history.

    Every render of the account page inside this rollout paid it, on a page
    served on every visit, and the process had nothing left over for anything
    else it was meant to be doing. Ordering the prices once costs about
    n log n comparisons; taking the cheapest that is left, over and over, costs
    about n squared over two. The bound below sits between the two.
    """
    how_many = 2_000
    prices = [CountedPrice((index * 7_919) % how_many) for index in range(how_many)]

    CountedPrice.comparisons = 0
    figure = typical_spend_per_item(an_account_of(*prices))
    comparisons = CountedPrice.comparisons

    assert figure == sorted(int(price) for price in prices)[(how_many - 1) // 2]
    assert comparisons < 20 * how_many, (
        f"the middle cost {comparisons} comparisons over {how_many} purchases"
    )
