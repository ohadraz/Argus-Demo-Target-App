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
# Nothing spent across nothing bought is nothing per item - see
# `average_spend_per_item_this_month`, where the case is argued at length.
NOTHING_SPENT_THIS_MONTH = 0


def average_spend_per_item(account: Account) -> int:
    """Average spend per item across everything this account has ever bought.

    Live for years. Its divisor is empty only for an account that has never
    bought anything at all, which no real shopper is - and where one does
    appear it is a broken record rather than a quiet month, so it raises and
    the boundary reports it rather than this inventing a figure for a history
    that is not there.
    """
    return account.total_cents // len(account.purchases)


def average_spend_per_item_this_month(account: Account) -> int:
    """Average spend per item this month.

    Narrows the lifetime figure to the current month, so the account page can
    show what a shopper is spending now rather than what they averaged over
    three years.

    A shopper who has bought nothing this month gets nothing, and that is the
    whole difference between this figure and the lifetime one above. The
    lifetime divisor is empty only for an account with no history at all; this
    one is empty for anybody who has not shopped since the first of the month,
    which on any ordinary day is a large share of the shop. Dividing by it was
    a division by zero waiting for the rollout that would reach those
    shoppers - and it did, taking the account page down for every one of them.

    Zero is the honest answer rather than a placeholder: the month is real, the
    shopper spent nothing in it, and "£0.00 per item this month" is what
    happened. That is unlike the statement panel, which has a largest, a
    smallest and a shape it genuinely cannot describe for an empty month and
    therefore still refuses to render one - see `io_shop.monthly_statement`.
    """
    bought_this_month = [
        purchase for purchase in account.purchases if purchase.in_current_month
    ]

    if not bought_this_month:
        return NOTHING_SPENT_THIS_MONTH

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
