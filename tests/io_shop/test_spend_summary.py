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

The case the flag broke is `a shopper who bought nothing this month`. It is not
an edge: on the second of the month it is nearly everybody, and while the flag
was off it was a divisor nobody ever reached.
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


def test_the_monthly_average_is_zero_for_a_shopper_who_bought_nothing_this_month(
) -> None:
    # The incident. This divisor is empty for anybody who has not shopped since
    # the first of the month, which is most of the shop most mornings - so the
    # monthly figure divided by zero for a third of traffic the moment the
    # rollout reached those shoppers. Nothing spent across nothing bought is
    # nothing per item, and it is a figure rather than a failure.
    account = an_account_with_no_purchases_this_month(1000, 3000)

    assert average_spend_per_item_this_month(account) == 0


def test_the_monthly_figure_survives_an_account_with_no_history_at_all() -> None:
    # An empty history has no purchases this month either, so the monthly
    # branch has to hold up here too - the rollout does not get to choose which
    # accounts it is pointed at.
    a_shopper_who_never_bought_anything = Account(
        shopper_id="shopper-with-no-history", purchases=(), total_cents=0,
        total_this_month_cents=0
    )

    assert average_spend_per_item_this_month(
        a_shopper_who_never_bought_anything
    ) == 0


def test_the_page_takes_the_stable_summary_when_the_rollout_is_off() -> None:
    account = an_account_with_no_purchases_this_month(1000, 3000)

    assert render_spend_summary(account, use_monthly_summary=False) == 2000


def test_the_page_renders_the_monthly_figure_when_the_rollout_is_on() -> None:
    # Through the selector rather than the function, because the selector is
    # what the flag drives and what every request goes through.
    account = an_account_with_no_purchases_this_month(1000, 3000)

    assert render_spend_summary(account, use_monthly_summary=True) == 0


def test_the_page_lets_a_failure_reach_its_caller() -> None:
    # Swallowing it here would render a wrong number instead of an error, and
    # there would be no error rate for anyone to alert on. The lifetime figure
    # keeps this deliberately: an account with no purchases at all is a broken
    # record rather than a quiet month, unlike the monthly figure above.
    a_shopper_who_never_bought_anything = Account(
        shopper_id="shopper-with-no-history", purchases=(), total_cents=0,
        total_this_month_cents=0
    )

    with pytest.raises(ZeroDivisionError):
        render_spend_summary(
            a_shopper_who_never_bought_anything, use_monthly_summary=False
        )
