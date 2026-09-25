from __future__ import annotations

import time
from pathlib import Path

from io_shop import payment_provider, spend_summary, summary_cache
from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.payment_provider import AskTheProvider, ProviderAnswer, StoredCard
from io_shop.summary_cache import CacheAnswer, CacheEndpoint, LookUpSummary

"""The shop's request boundary: what a caller sees when the page fails.

Four things worth pinning: a failure is reported rather than raised - a handler
that let it escape would take the worker down - the report keeps the error's own
words, which are what a reader diagnoses from, a payment provider that will
not answer fails the page in words that name the provider rather than the shop,
and an upstream that answers too slowly is given up on rather than waited for.
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


def a_cache_holding(summary_cents: int) -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(
        reached=True, summary_cents=summary_cents
    )


def a_cache_holding_nothing() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=True)


def a_cache_that_cannot_be_reached() -> LookUpSummary:
    return lambda dont_care_shopper: CacheAnswer(reached=False)


# Ten times what the page will wait, so a render that still waited it out is
# unmistakable in the timing rather than a matter of tolerance.
SLOWER_THAN_THE_SHOP_WILL_WAIT = (
    summary_cache.HOW_LONG_THE_SHOP_WAITS_SECONDS * 10
)


def a_cache_too_slow_to_be_worth_waiting_for() -> LookUpSummary:
    def look_up(dont_care_shopper: str) -> CacheAnswer:
        time.sleep(SLOWER_THAN_THE_SHOP_WILL_WAIT)

        return CacheAnswer(reached=True, summary_cents=999)

    return look_up


SOME_CACHE_ENDPOINT = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6379)


def test_a_page_whose_figure_was_cached_says_so() -> None:
    page = serve_account_page(an_account_idle_this_month(1000, 3000),
                              use_monthly_summary=False,
                              ask_the_provider=a_provider_holding_a_card(),
                              look_up_summary=a_cache_holding(999),
                              cache_endpoint=SOME_CACHE_ENDPOINT)

    assert page.figure_cents == 999
    assert page.served_from_cache


def test_a_miss_is_computed_and_the_page_is_still_correct() -> None:
    account = an_account_idle_this_month(1000, 3000)

    computed = serve_account_page(account,
                                  use_monthly_summary=False,
                                  ask_the_provider=a_provider_holding_a_card(),
                                  look_up_summary=a_cache_holding_nothing(),
                                  cache_endpoint=SOME_CACHE_ENDPOINT)
    without_a_cache_at_all = serve_account_page(
        account,
        use_monthly_summary=False,
        ask_the_provider=a_provider_holding_a_card()
    )

    assert computed.figure_cents == without_a_cache_at_all.figure_cents
    assert not computed.served_from_cache


def test_a_cache_nobody_can_reach_does_not_fail_the_page() -> None:
    # The whole scenario rests on this. The fallback is the designed behaviour,
    # so losing the cache is a slowdown and not an outage - which is exactly
    # why nobody notices it in the error rate.
    account = an_account_idle_this_month(1000, 3000)

    page = serve_account_page(account,
                              use_monthly_summary=False,
                              ask_the_provider=a_provider_holding_a_card(),
                              look_up_summary=a_cache_that_cannot_be_reached(),
                              cache_endpoint=SOME_CACHE_ENDPOINT)

    assert page.failure is None
    assert page.figure_cents is not None
    assert not page.served_from_cache


def test_an_unreachable_cache_is_reported_beside_the_failure_not_in_it() -> None:
    # A page that succeeded while something underneath it was broken. The
    # distinction is the incident: a reader sees this in the logs without
    # seeing it in the error rate.
    page = serve_account_page(an_account_idle_this_month(1000, 3000),
                              use_monthly_summary=False,
                              ask_the_provider=a_provider_holding_a_card(),
                              look_up_summary=a_cache_that_cannot_be_reached(),
                              cache_endpoint=SOME_CACHE_ENDPOINT)

    assert page.failure is None
    assert page.cache_failure is not None
    assert "6379" in page.cache_failure


def test_a_cache_too_slow_to_wait_for_does_not_slow_the_page() -> None:
    # The incident, in miniature. The upstream answers - correctly, and far too
    # late - and a page that waited for it would take the upstream's latency on
    # every render, which is exactly what the shop's own figures showed. The
    # fallback the cache exists to permit is reached in time instead: the page
    # is correct, says it was not served from the cache, and reports the slow
    # upstream beside the response rather than in the error rate.
    account = an_account_idle_this_month(1000, 3000)

    started = time.monotonic()
    page = serve_account_page(account,
                              use_monthly_summary=False,
                              ask_the_provider=a_provider_holding_a_card(),
                              look_up_summary=a_cache_too_slow_to_be_worth_waiting_for(),
                              cache_endpoint=SOME_CACHE_ENDPOINT)
    took = time.monotonic() - started

    assert page.failure is None
    assert page.figure_cents == 2000
    assert not page.served_from_cache
    assert page.cache_failure is not None
    assert took < SLOWER_THAN_THE_SHOP_WILL_WAIT / 2
