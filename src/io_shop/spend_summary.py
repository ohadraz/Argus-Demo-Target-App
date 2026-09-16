from __future__ import annotations

from io_shop.accounts import Account

"""What a shopper has spent, and what that averages to.

Two figures with the same shape, but not the same divisor. The lifetime average
has been on the account page for years; the monthly one is new, narrows it to
the current month, and ships behind the `monthly-spend-feature` flag while the
rollout runs.

The narrowing is what makes the divisors different. Lifetime purchases are empty
only for a shopper who has never bought anything at all; this month's purchases
are empty for every shopper who simply has not bought anything lately, which is
an ordinary state for a large share of the page's traffic. The monthly figure
therefore has to answer for an empty month rather than divide by it.
"""


def average_spend_per_item(account: Account) -> int:
    """Average spend per item across everything this account has ever bought.

    Live for years. Its divisor is empty only for an account that has never
    bought anything at all, which no real shopper is.
    """
    return account.total_cents // len(account.purchases)


def average_spend_per_item_this_month(account: Account) -> int:
    """Average spend per item this month.

    Narrows the lifetime figure to the current month, so the account page can
    show what a shopper is spending now rather than what they averaged over
    three years.

    A shopper who has bought nothing this month has nothing to average, so the
    figure is 0 rather than a division by an empty month. That is not an
    exceptional account - it is most accounts on most days - and treating it as
    one is what took the account page down when this figure was first rolled
    out.
    """
    bought_this_month = [
        purchase for purchase in account.purchases if purchase.in_current_month
    ]

    if not bought_this_month:
        return 0

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
