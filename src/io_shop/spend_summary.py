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

    A month with nothing bought in it averages to nothing, and the guard below
    is the difference between this figure and the lifetime one beside it. The
    lifetime divisor is empty only for an account that has never bought
    anything, which is nobody; this divisor is empty for any shopper who has
    simply not bought yet this month, which at the start of a month is almost
    everybody. Narrowing the figure to the month narrowed its divisor too, and
    an empty one here is the ordinary state of an ordinary account rather than
    a history that cannot exist.

    Zero rather than a raise, and rather than the lifetime figure: the question
    the page is asking is what this shopper is spending *now*, and the true
    answer for a month with no purchases in it is nothing. Falling back to the
    lifetime average would answer a different question under this figure's
    label, which is the one outcome worse than an empty panel - a plausible
    number nobody can tell is from the wrong month.
    """
    bought_this_month = [
        purchase for purchase in account.purchases if purchase.in_current_month
    ]

    if not bought_this_month:
        return 0

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
