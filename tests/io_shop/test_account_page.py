from __future__ import annotations

from pathlib import Path

from io_shop import payment_provider, spend_summary
from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.payment_provider import AskTheProvider, ProviderAnswer, StoredCard

"""The shop's request boundary: what a caller sees when the page fails.

Three things worth pinning: a failure is reported rather than raised - a handler
that let it escape would take the worker down - the report keeps the error's own
words, which are what a reader diagnoses from, and a payment provider that will
not answer fails the page in words that name the provider rather than the shop.
"""


def a_provider_holding_a_card() -> AskTheProvider:
    return lambda dont_care_shopper: ProviderAnswer(
        status=200, card=StoredCard(brand="visa", last_four="4242")
    )


def a_provider_that_is_down() -> AskTheProvider:
    return lambda dont_care_shopper: ProviderAnswer(status=503)


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

    page = serve_account_page(account,
                              use_monthly_summary=False,
                              ask_the_provider=a_provider_holding_a_card())

    assert page.figure_cents == 2000
    assert page.card_last_four == "4242"
    assert page.failure is None


def test_a_page_that_breaks_is_reported_rather_than_raised() -> None:
    page = serve_account_page(
        an_account_that_never_bought_anything(),
        use_monthly_summary=False,
        ask_the_provider=a_provider_holding_a_card()
    )

    assert page.figure_cents is None
    assert page.failure is not None


def test_a_failure_keeps_the_errors_own_words() -> None:
    # "request failed" describes every incident equally. The type and message
    # are what put `ZeroDivisionError` in the logs an investigation reads.
    page = serve_account_page(
        an_account_that_never_bought_anything(),
        use_monthly_summary=False,
        ask_the_provider=a_provider_holding_a_card()
    )

    assert page.failure is not None
    assert page.failure.startswith("ZeroDivisionError: division by zero")


def test_a_failure_names_the_line_it_was_raised_on() -> None:
    # The innermost frame, not the boundary's own: every failure in the shop is
    # caught in the same place, so the boundary's line describes all of them and
    # locates none. This is the one that divided by zero.
    page = serve_account_page(
        an_account_that_never_bought_anything(),
        use_monthly_summary=False,
        ask_the_provider=a_provider_holding_a_card()
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
        an_account_that_never_bought_anything(),
        use_monthly_summary=False,
        ask_the_provider=a_provider_holding_a_card()
    )

    assert page.failure is not None
    named = int(page.failure.rsplit(":", 1)[1])

    assert "//" in source[named - 1]


def test_a_provider_that_will_not_answer_fails_the_page() -> None:
    # Nothing is retried and nothing is rendered without the card: when the
    # provider is down there is nothing the shop can do about it, and code that
    # softened this would turn somebody else's outage into a question about Io's
    # resilience.
    page = serve_account_page(an_account_idle_this_month(1000, 3000),
                              use_monthly_summary=False,
                              ask_the_provider=a_provider_that_is_down())

    assert page.figure_cents is None
    assert page.card_last_four is None
    assert page.failure is not None


def test_a_provider_failure_names_the_provider_and_the_status() -> None:
    # The host and the status, because those are what tell a reader at three in
    # the morning that the fault is not in this repository.
    page = serve_account_page(an_account_idle_this_month(1000, 3000),
                              use_monthly_summary=False,
                              ask_the_provider=a_provider_that_is_down())

    assert page.failure is not None
    assert page.failure.startswith("PaymentProviderFailed: ")
    assert payment_provider.PROVIDER_HOST in page.failure
    assert "503" in page.failure
