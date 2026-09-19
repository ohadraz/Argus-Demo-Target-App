from __future__ import annotations

from pathlib import Path

from io_shop import spend_summary
from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase

"""The shop's request boundary: what a caller sees when the page fails.

Two things worth pinning: a failure is reported rather than raised - a handler
that let it escape would take the worker down - and the report keeps the error's
own words, which are what a reader diagnoses from.
"""


def an_account_idle_this_month(*prices: int) -> Account:
    return Account(
        shopper_id="shopper-idle-this-month",
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
    return Account(
        shopper_id="shopper-with-no-history", purchases=(), total_cents=0,
        total_this_month_cents=0
    )


def test_a_page_that_renders_carries_the_figure_and_no_failure() -> None:
    account = an_account_idle_this_month(1000, 3000)

    page = serve_account_page(account, use_monthly_summary=False)

    assert page.figure_cents == 2000
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

    assert page.failure is not None
    assert page.failure.startswith("ZeroDivisionError: division by zero")


def test_a_failure_names_the_line_it_was_raised_on() -> None:
    # The innermost frame, not the boundary's own: every failure in the shop is
    # caught in the same place, so the boundary's line describes all of them and
    # locates none. This is the one that divided by zero.
    page = serve_account_page(
        an_account_that_never_bought_anything(), use_monthly_summary=False
    )

    assert page.failure is not None
    assert "at src/io_shop/spend_summary.py:" in page.failure


def test_the_line_a_failure_names_is_the_one_that_raised_it() -> None:
    # The number, not just the file. A frame reported off by a few lines sends a
    # reader to code that is fine and reads as authoritatively as a right one.
    source = (
        Path(spend_summary.__file__).read_text(encoding="utf-8").splitlines()
    )
    page = serve_account_page(
        an_account_that_never_bought_anything(), use_monthly_summary=False
    )

    assert page.failure is not None
    named = int(page.failure.rsplit(":", 1)[1])

    assert "//" in source[named - 1]
