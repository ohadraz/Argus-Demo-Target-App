from __future__ import annotations

from io_shop.accounts import Account
from io_shop.typical_spend import typical_spend_per_item

"""What a shopper has spent, and what that averages to.

Three figures about one history. The lifetime average has been on the account
page for years; the monthly one narrows it to the current month; the typical
purchase abandons the average altogether for the middle of the history. The
last two are the ones still behind a rollout, and which of them a request gets
is decided before it reaches here.
"""

# What the monthly figure comes to for a shopper who bought nothing this month.
# Not a fallback and not a guess: nothing spent across nothing bought is zero
# spent per item, and it is the figure the page would show if the month had one
# free purchase in it. Named so that the reason sits beside the value.
NOTHING_SPENT_YET_THIS_MONTH = 0


def average_spend_per_item(account: Account) -> int:
    """Average spend per item across everything this account has ever bought.

    Live for years. Its divisor is empty only for an account that has never
    bought anything at all - a shopper with no history has no average, there is
    no honest figure to show them, and the boundary above turns that into a
    reported failure rather than a made-up number. That is a different
    situation from the monthly figure below, where an empty divisor is an
    ordinary month rather than a missing shopper.
    """
    return account.total_cents // len(account.purchases)


def average_spend_per_item_this_month(account: Account) -> int:
    """Average spend per item this month.

    Narrows the lifetime figure to the current month, so the account page can
    show what a shopper is spending now rather than what they averaged over
    three years.

    A month with nothing in it is answered with zero rather than raised on, and
    that is the whole difference between this figure and the lifetime one. The
    lifetime divisor is empty only for a shopper who has never bought anything;
    this one is empty for every shopper who simply has not been in yet this
    month, which is a large and entirely ordinary share of the shop on any
    given day. A figure that failed for them would fail for a third of the
    account pages served - which is exactly what happened the first time this
    path carried the traffic - and it would fail describing a shopper about
    whom nothing is wrong. They spent nothing, so the figure is nothing.
    """
    bought_this_month = [
        purchase for purchase in account.purchases if purchase.in_current_month
    ]

    if not bought_this_month:
        return NOTHING_SPENT_YET_THIS_MONTH

    return account.total_this_month_cents // len(bought_this_month)


def render_spend_summary(account: Account,
                         use_monthly_summary: bool,
                         use_typical_spend: bool = False) -> int:
    """The figure Io's account page shows, through whichever version the rollout
    selects.

    Raises whatever the selected version raises. Catching belongs at the request
    boundary - see `io_shop.account_page` - where there is a response to turn a
    failure into and a log to record it in. A page that swallowed its own errors
    would render a wrong number instead of an error, which is a worse incident
    than the one it hid.

    Two rollouts, so two flags to be told about, and the newer one wins where a
    request is inside both. That is the ordinary arrangement: each is a slice of
    traffic, the slices overlap, and the page has to render one figure.
    """
    if use_typical_spend:
        return typical_spend_per_item(account)

    if use_monthly_summary:
        return average_spend_per_item_this_month(account)

    return average_spend_per_item(account)
