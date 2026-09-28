from __future__ import annotations

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.spend_summary import (
    average_spend_per_item,
    average_spend_per_item_this_month,
    render_spend_summary,
)

"""Io's account-page arithmetic - the lifetime figure and the monthly one.

The monthly figure is newer and ships behind a rollout flag, so the cases below
cover the shape the page has had for years plus the one the flag adds.
"""


def an_account_with_no_purchases_this_month(*prices: int) -> Account:
    """The ordinary case: a shopper who has bought before, but not this month."""
    return Account(
        shopper_id="shopper-idle-this-month",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )


class CountingPurchases:
    """A purchase history that records how many purchases were read out of it.

    Stands in for the real tuple so a test can measure the work the figure
    costs rather than only the number it produces.
    """

    def __init__(self, purchases: tuple[Purchase, ...]) -> None:
        self._purchases = purchases
        self.purchases_read = 0

    def __len__(self) -> int:
        return len(self._purchases)

    def __iter__(self):
        for purchase in self._purchases:
            self.purchases_read += 1
            yield purchase

    def __getitem__(self, index):
        if isinstance(index, slice):
            sliced = self._purchases[index]
            self.purchases_read += len(sliced)
            return sliced
        self.purchases_read += 1
        return self._purchases[index]


def test_the_lifetime_average_spreads_the_total_over_every_purchase() -> None:
    account = an_account_with_no_purchases_this_month(1000, 2000, 3000)

    assert average_spend_per_item(account) == 2000


def test_the_lifetime_average_follows_the_purchases_not_the_carried_total() -> None:
    # The figure is derived from the list the shopper is looking at, so a total
    # that has drifted from its own purchases does not reach the page.
    an_account_whose_total_drifted = Account(
        shopper_id="shopper-with-a-stale-total",
        purchases=(
            Purchase(price_cents=1000, in_current_month=False),
            Purchase(price_cents=3000, in_current_month=False),
        ),
        total_cents=99_999,
        total_this_month_cents=0,
    )

    assert average_spend_per_item(an_account_whose_total_drifted) == 2000


def test_the_lifetime_average_reads_each_purchase_a_bounded_number_of_times() -> None:
    # The figure has to cost one pass over the history. Recomputing the running
    # total inside a loop reads roughly n**2 / 2 purchases - 20,000 for the
    # history below - and that is the CPU time a long history spends in the
    # request.
    a_long_history = tuple(
        Purchase(price_cents=100 + price, in_current_month=False)
        for price in range(200)
    )
    counted = CountingPurchases(a_long_history)
    an_account_with_a_long_history = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=counted,  # type: ignore[arg-type]
        total_cents=sum(purchase.price_cents for purchase in a_long_history),
        total_this_month_cents=0,
    )

    average_spend_per_item(an_account_with_a_long_history)

    assert counted.purchases_read <= 2 * len(a_long_history)


def test_the_monthly_average_is_correct_for_a_shopper_who_did_buy() -> None:
    # The ordinary case for the monthly figure: a shopper who has bought
    # something this month, averaged over what they bought this month.
    an_active_shopper = Account(
        shopper_id="shopper-who-bought-this-month",
        purchases=(
            Purchase(price_cents=4000, in_current_month=True),
            Purchase(price_cents=2000, in_current_month=True),
            Purchase(price_cents=9000, in_current_month=False),
        ),
        total_cents=15000,
        total_this_month_cents=6000,
    )

    assert average_spend_per_item_this_month(an_active_shopper) == 3000


def test_the_page_takes_the_stable_summary_when_the_rollout_is_off() -> None:
    account = an_account_with_no_purchases_this_month(1000, 3000)

    assert render_spend_summary(account, use_monthly_summary=False) == 2000


def test_the_page_lets_a_failure_reach_its_caller() -> None:
    # Swallowing it here would render a wrong number instead of an error, and
    # there would be no error rate for anyone to alert on.
    a_shopper_who_never_bought_anything = Account(
        shopper_id="shopper-with-no-history", purchases=(), total_cents=0,
        total_this_month_cents=0
    )

    with pytest.raises(ZeroDivisionError):
        render_spend_summary(
            a_shopper_who_never_bought_anything, use_monthly_summary=False
        )
