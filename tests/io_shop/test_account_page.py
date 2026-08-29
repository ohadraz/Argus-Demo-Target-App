from __future__ import annotations

from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase

"""The shop's request boundary: what a caller sees when the page fails.

This is where the demo's whole error rate comes from, so the two things worth
pinning are that a failure is reported rather than raised - a handler that let it
escape would take the worker down instead of producing a rate - and that the
failure keeps the error's own words, which are what a reader diagnoses from.
"""


def an_account_idle_this_month(*prices: int) -> Account:
    return Account(
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )


def test_a_page_that_renders_carries_the_figure_and_no_failure() -> None:
    account = an_account_idle_this_month(1000, 3000)

    page = serve_account_page(account, use_monthly_summary=False)

    assert page.figure_cents == 2000
    assert page.failure is None


def test_a_page_that_breaks_is_reported_rather_than_raised() -> None:
    account = an_account_idle_this_month(1000, 3000)

    page = serve_account_page(account, use_monthly_summary=True)

    assert page.figure_cents is None
    assert page.failure is not None


def test_a_failure_keeps_the_errors_own_words() -> None:
    # "request failed" describes every incident equally. The type and message
    # are what put `ZeroDivisionError` in the logs an investigation reads.
    account = an_account_idle_this_month(1000, 3000)

    page = serve_account_page(account, use_monthly_summary=True)

    assert page.failure == "ZeroDivisionError: division by zero"
