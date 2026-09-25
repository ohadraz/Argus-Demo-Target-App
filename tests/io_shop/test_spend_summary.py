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
cover the shape the page has had for years plus the one the flag adds - and what
each of them costs, because the page runs them while it renders.
"""


class CountingPurchase(Purchase):
    """A purchase that records every time its price is read.

    Reading the price is the unit of work the lifetime average does; counting
    the reads says whether the history is walked once or once per purchase,
    without timing anything.
    """

    reads = 0

    @property  # type: ignore[misc]
    def price_cents(self) -> int:  # type: ignore[override]
        CountingPurchase.reads += 1
        return self.__dict__["_price_cents"]


def a_counting_purchase(price: int) -> CountingPurchase:
    purchase = CountingPurchase(price_cents=price, in_current_month=False)
    object.__setattr__(purchase, "_price_cents", price)
    return purchase


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


def test_a_long_history_is_summed_once_rather_than_re_summed_per_purchase() -> None:
    # The incident: re-summing everything bought so far at every purchase reads
    # each price once per purchase - n^2/2 reads over a history - and the page
    # that renders this figure pays for it on every request that misses cache.
    history = 400
    purchases = tuple(a_counting_purchase(i + 1) for i in range(history))
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=purchases,
        total_cents=0,
        total_this_month_cents=0,
    )

    CountingPurchase.reads = 0

    assert average_spend_per_item(account) == sum(range(1, history + 1)) // history
    assert CountingPurchase.reads <= history * 2


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
