from __future__ import annotations

import pytest
from io_shop.accounts import Account, Purchase
from io_shop.spend_summary import (
    average_spend_per_item,
    average_spend_per_item_this_month,
    render_spend_summary,
)

"""Io's account-page arithmetic, including the fault the demo is built around.

The monthly test is the odd one here: it asserts that code is broken, and it has
to, because that break is the incident. If it ever starts passing, either
someone fixed the bug - in which case the scenario built on it stages nothing -
or the account being summarised no longer looks like most real accounts.
"""


def an_account_with_no_purchases_this_month(*prices: int) -> Account:
    """The ordinary case: a shopper who has bought before, but not this month."""
    return Account(
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )


def test_the_lifetime_average_spreads_the_total_over_every_purchase() -> None:
    account = an_account_with_no_purchases_this_month(1000, 2000, 3000)

    assert average_spend_per_item(account) == 2000


def test_the_monthly_average_divides_by_zero_for_a_shopper_idle_this_month() -> None:
    # The seeded fault. The figure is scoped to the current month, and most
    # shoppers bought nothing in the current month - so the divisor is zero for
    # most of the traffic, not for an unlucky few.
    account = an_account_with_no_purchases_this_month(1000, 2000, 3000)

    with pytest.raises(ZeroDivisionError):
        average_spend_per_item_this_month(account)


def test_the_monthly_average_is_correct_for_a_shopper_who_did_buy() -> None:
    # Pins what is actually wrong. The arithmetic is fine; the set it averages
    # over is empty for most of the people it is shown to. A fix that made this
    # case wrong would be fixing the wrong thing.
    an_active_shopper = Account(
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


def test_the_page_lets_the_monthly_failure_reach_its_caller() -> None:
    # Swallowing it here would render a wrong number instead of an error, and
    # there would be no error rate for anyone to alert on.
    account = an_account_with_no_purchases_this_month(1000, 3000)

    with pytest.raises(ZeroDivisionError):
        render_spend_summary(account, use_monthly_summary=True)
