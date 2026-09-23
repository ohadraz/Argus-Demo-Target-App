from __future__ import annotations

from io_shop.accounts import Account

"""The account page's single-figure spend summaries.

One number per function, each of them an average of something. The statement
panel next door says the month in full - see `io_shop.monthly_statement` - and
these are the figures that were on the page before it and are still on the page
beside it.

Every average here divides by a count that comes from the shopper's history,
and a shopper who bought nothing this month makes that count zero. That is not
an error, a corruption or a missing record: it is the single most ordinary
thing an account can look like on the first of the month, and it is the shape
that took the account page down when the panel behind `monthly-spend-feature`
first saw real traffic. So every divisor below is checked, once, at the point
it is used.
"""

# What an average comes to when there is nothing to average. Zero rather than
# `None`, because every caller of these functions formats the result as money
# and a figure of nothing spent across nothing bought is nothing - and because
# a page that has to check for `None` on four figures is a page that will one
# day check on three.
NOTHING_SPENT = 0


def purchases_this_month(account: Account) -> list:
    """Everything this shopper bought in the current month.

    The purchase carries the month rather than a date, because that is what the
    query this account came back from selected on - see `io_shop.accounts`.
    """
    return [purchase for purchase in account.purchases if purchase.in_current_month]


def average_spend_per_item_this_month(account: Account) -> int:
    """What the month's purchases averaged, in pence.

    Floor division, so the figure is never a fraction of a penny.

    A month with no purchases in it averages to nothing rather than raising.
    There is genuinely no average to report - the divisor is zero - and the
    honest reading of "spent nothing across nothing" is zero, not a failed
    account page. Returning a figure here is what keeps a shopper who has not
    bought anything yet this month able to see the rest of their account.
    """
    bought = purchases_this_month(account)

    if not bought:
        return NOTHING_SPENT

    return account.total_this_month_cents // len(bought)


def average_spend_per_item_lifetime(account: Account) -> int:
    """What this shopper's purchases have averaged across their whole history.

    Guarded the same way and for the same reason: a brand new account has no
    purchases at all, which is every account for the few seconds between being
    created and being used.
    """
    if not account.purchases:
        return NOTHING_SPENT

    return account.total_cents // len(account.purchases)
</content>