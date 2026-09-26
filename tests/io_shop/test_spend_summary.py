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
cover the shape the page has had for years plus the one the flag adds - and
what the lifetime figure costs, because it is walked on every request the
rollout does not reach.
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


_PRICE_READS = [0]


class _CountedPurchase:
    """A purchase that remembers how often its price was read.

    Everything the lifetime figure touches and nothing else, so what the count
    measures is how many times the history was walked.
    """

    in_current_month = False

    def __init__(self, price_cents: int) -> None:
        self._price_cents = price_cents

    @property
    def price_cents(self) -> int:
        _PRICE_READS[0] += 1
        return self._price_cents


def test_the_lifetime_average_reads_each_price_about_once() -> None:
    # Re-summing the history once per purchase gives the right answer at a cost
    # that grows with the square of the history, which is paid by the shoppers
    # who have bought the most - the same tail that the rollout's figure made
    # worse. Adding the prices up once is a single pass, so a bound of two
    # reads per purchase fails against the loop and passes against the sum.
    how_many = 400
    purchases = tuple(
        _CountedPurchase(price_cents=100 + index) for index in range(how_many)
    )
    a_long_history = Account(
        shopper_id="shopper-with-a-very-long-history",
        purchases=purchases,  # type: ignore[arg-type]
        total_cents=0,
        total_this_month_cents=0,
    )
    expected = sum(100 + index for index in range(how_many)) // how_many

    _PRICE_READS[0] = 0
    average = average_spend_per_item(a_long_history)
    reads = _PRICE_READS[0]

    assert average == expected
    assert reads <= how_many * 2


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
