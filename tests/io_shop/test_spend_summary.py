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

One case is about cost rather than correctness. These figures are what the page
falls back to when the summary cache cannot be reached, so what they cost is
the shop's latency on the day the cache goes away - and a test that checked
only the number would pass just as happily against a version that took a
hundred times as long to produce it.
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


class CountedPurchase:
    """A purchase that remembers how often its price was read.

    Stands in for a `Purchase` rather than subclassing one, because `Purchase`
    is frozen and what is being counted is attribute reads. Nothing in the
    summary asks a purchase for anything a real one would not answer.
    """

    def __init__(self, price_cents: int, reads: list[int]) -> None:
        self._price_cents = price_cents
        self._reads = reads
        self.in_current_month = False

    @property
    def price_cents(self) -> int:
        self._reads[0] += 1
        return self._price_cents


def test_the_lifetime_average_reads_each_purchase_a_bounded_number_of_times() -> None:
    # The figure is what the account page computes for itself whenever the
    # summary cache cannot be reached, so its cost is the shop's latency on
    # the day the cache is gone. Summing the history afresh once per purchase
    # produced the same number while doing n-squared work - 45,150 reads for
    # the history below - and that is what turned an optional cache into a
    # load-bearing one.
    how_many = 300
    reads = [0]
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            CountedPurchase(100 + index, reads) for index in range(how_many)
        ),
        total_cents=0,
        total_this_month_cents=0,
    )

    figure = average_spend_per_item(account)

    assert figure == sum(100 + index for index in range(how_many)) // how_many
    assert reads[0] <= 3 * how_many
