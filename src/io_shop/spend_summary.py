from __future__ import annotations

from io_shop.accounts import Account

"""What a shopper has spent, and what that averages to.

**This is where the bug is.** The account page is getting a new figure - average
spend per item *this month* - and it ships behind the `monthly-spend-feature`
flag. The new version divides by the number of items bought this month, and most
shoppers bought none, so it raises `ZeroDivisionError` for most of the traffic it
is shown to.

The defect is left here deliberately: it is the fault every seeded incident is
caused by, and the one a code fix has to find. It is real code, really executed -
the shop's error rate counts exceptions that were genuinely raised.
"""


def average_spend_per_item(account: Account) -> int:
    """Average spend per item across everything this account has ever bought.

    The version that has been live for years. Its divisor is empty only for an
    account that has never bought anything at all, which is rare enough that
    nobody ever hit it - which is exactly why the same mistake went unnoticed
    when the figure was narrowed to a month.
    """
    return account.total_cents // len(account.purchases)


def average_spend_per_item_this_month(account: Account) -> int:
    """Average spend per item this month.

    Narrows the lifetime figure to the current month, so the account page can
    show what a shopper is spending now rather than what they averaged over
    three years.
    """
    bought_this_month = [
        purchase for purchase in account.purchases if purchase.in_current_month
    ]
    return account.total_this_month_cents // len(bought_this_month)


def render_spend_summary(account: Account, use_monthly_summary: bool) -> int:
    """The figure Io's account page shows, through whichever version the rollout
    selects.

    Raises whatever the selected version raises. Catching belongs at the request
    boundary - see `io_shop.account_page` - where there is a response to turn a
    failure into and a log to record it in. A page that swallowed its own errors
    would render a wrong number instead of an error, which is a worse incident
    than the one it hid.
    """
    if use_monthly_summary:
        return average_spend_per_item_this_month(account)

    return average_spend_per_item(account)
