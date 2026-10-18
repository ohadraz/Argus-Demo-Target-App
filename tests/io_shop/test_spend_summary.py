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


_PURCHASES_READ = [0]


class _CountingPurchases(tuple):  # type: ignore[type-arg]
    """A purchase history that counts every purchase iterated over, including
    through slices of itself."""

    def __iter__(self):  # type: ignore[no-untyped-def]
        for purchase in super().__iter__():
            _PURCHASES_READ[0] += 1
            yield purchase

    def __getitem__(self, key):  # type: ignore[no-untyped-def]
        found = super().__getitem__(key)
        if isinstance(key, slice):
            return _CountingPurchases(found)
        return found


def test_the_lifetime_average_reads_each_purchase_a_bounded_number_of_times() -> None:
    # The incident: re-summing every prefix of the history made each page
    # quadratic in the shopper's history, so CPU per request was far higher
    # than the figure needs and a traffic ramp pinned the CPU limit.
    n = 2000
    purchases = _CountingPurchases(
        Purchase(price_cents=100 + i, in_current_month=False) for i in range(n)
    )
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=purchases,
        total_cents=0,
        total_this_month_cents=0,
    )

    _PURCHASES_READ[0] = 0
    figure = average_spend_per_item(account)

    assert figure == sum(100 + i for i in range(n)) // n
    assert _PURCHASES_READ[0] <= 2 * n


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
