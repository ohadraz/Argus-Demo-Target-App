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

One case covers what the lifetime figure costs rather than what it says. That
is not tidiness: the figure is worked out on every render of the shop's
busiest page, so a cost that follows the square of a shopper's history is a CPU
limit reached the first morning traffic doubles.
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


class CountingPurchase:
    """A purchase that remembers how often its price was read.

    Stands in for `Purchase` where what is under test is the work done rather
    than the figure produced - the page reads prices, and how many reads one
    render costs is the thing that saturated three cores.
    """

    def __init__(self, price_cents: int) -> None:
        self._price_cents = price_cents
        self.in_current_month = False
        self.reads = 0

    @property
    def price_cents(self) -> int:
        self.reads += 1
        return self._price_cents


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


def test_the_lifetime_average_reads_each_purchase_once() -> None:
    # What this asserts is cost, not correctness. Re-summing the history once
    # per purchase gives the same number for n(n+1)/2 reads instead of n, and
    # that difference is the difference between fitting inside the CPU limit at
    # four times the traffic and pinning it.
    history = [CountingPurchase(price_cents=100 + index) for index in range(200)]
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(history),  # type: ignore[arg-type]
        total_cents=0,
        total_this_month_cents=0,
    )

    figure = average_spend_per_item(account)
    reads = sum(purchase.reads for purchase in history)

    assert figure == sum(100 + index for index in range(200)) // 200
    assert reads <= 2 * len(history), (
        f"{reads} price reads for {len(history)} purchases - the figure is "
        "being worked out in more than one pass over the history"
    )


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
