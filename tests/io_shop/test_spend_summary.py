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

One case covers what the arithmetic costs rather than what it says. This is the
figure a request falls back to when the cache holds nothing or cannot be
reached, so every request can be running it at once, and work that grows with
the square of a history turns a lost cache into a latency incident. The cost is
counted, not timed: a clock would measure the machine the test ran on.
"""


class _CountedPrice(int):
    """A price that tallies every addition and comparison it takes part in.

    A price is only ever added up or compared, so the tally is a faithful count
    of the work a figure does over a history - and unlike a stopwatch it is the
    same number on every machine.
    """

    operations = 0

    @classmethod
    def reset(cls) -> None:
        cls.operations = 0

    def _counted(self) -> None:
        type(self).operations += 1

    def __add__(self, other: int) -> int:  # type: ignore[override]
        self._counted()
        return int(self) + int(other)

    __radd__ = __add__

    def __lt__(self, other: int) -> bool:
        self._counted()
        return int(self) < int(other)

    def __gt__(self, other: int) -> bool:
        self._counted()
        return int(self) > int(other)

    def __eq__(self, other: object) -> bool:
        self._counted()
        return int(self) == other

    __hash__ = int.__hash__


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


def test_the_lifetime_average_costs_a_pass_over_the_history_not_a_pass_each() -> None:
    # The cache-miss path. Rebuilding the running total at every index gave the
    # right figure for a cost that grows with the square of the history - fine
    # while the cache hit almost everything, and the page's whole latency the
    # minute the cache went away.
    how_many = 200
    a_long_history = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(
            Purchase(price_cents=_CountedPrice(1000 + index), in_current_month=False)
            for index in range(how_many)
        ),
        total_cents=0,
        total_this_month_cents=0,
    )

    _CountedPrice.reset()
    figure = average_spend_per_item(a_long_history)

    assert figure == (sum(1000 + index for index in range(how_many)) // how_many)
    # One pass is `how_many` additions; a pass per purchase would be ~20,000.
    assert _CountedPrice.operations <= 5 * how_many


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
