from __future__ import annotations

from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase

"""The shop's request boundary: what a caller sees when the page fails.

Two things worth pinning: a failure is reported rather than raised - a handler
that let it escape would take the worker down - and the report keeps the error's
own words, which are what a reader diagnoses from.
"""


def an_account_idle_this_month(*prices: int) -> Account:
    return Account(
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )


def an_account_that_never_bought_anything() -> Account:
    """A shopper with no purchase history at all - the lifetime figure's own
    empty divisor, and the failure this file is built on.
    """
    return Account(purchases=(), total_cents=0, total_this_month_cents=0)


def test_a_page_that_renders_carries_the_figure_and_no_failure() -> None:
    account = an_account_idle_this_month(1000, 3000)

    page = serve_account_page(account, use_monthly_summary=False)

    assert page.figure_cents == 2000
    assert page.failure is None


def test_the_monthly_rollout_renders_for_a_shopper_idle_this_month() -> None:
    # The incident's request, with the flag on: it used to come back as a
    # ZeroDivisionError failure for every shopper who had not bought this month.
    account = an_account_idle_this_month(1000, 3000)

    page = serve_account_page(account, use_monthly_summary=True)

    assert page.figure_cents == 0
    assert page.failure is None


def test_a_page_that_breaks_is_reported_rather_than_raised() -> None:
    page = serve_account_page(
        an_account_that_never_bought_anything(), use_monthly_summary=False
    )

    assert page.figure_cents is None
    assert page.failure is not None


def test_a_failure_keeps_the_errors_own_words() -> None:
    # "request failed" describes every incident equally. The type and message
    # are what put `ZeroDivisionError` in the logs an investigation reads.
    page = serve_account_page(
        an_account_that_never_bought_anything(), use_monthly_summary=False
    )

    assert page.failure == "ZeroDivisionError: division by zero"
