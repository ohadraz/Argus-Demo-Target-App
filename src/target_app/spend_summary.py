from __future__ import annotations

from dataclasses import dataclass

"""Io's account page: what a shopper has spent, and what that averages to.

Real code, really run. The telemetry this service serves is produced by calling
`render_spend_summary` and recording what happened, rather than by describing
what would have happened - so the error rate on a bad minute counts exceptions
that were genuinely raised, and the log lines quote a failure that genuinely
occurred.

The monthly summary carries a defect. It is left here deliberately: it is the
fault a seeded incident is caused by, and the one a code fix has to find.
"""

# What fraction of traffic the new summary is rolled out to while its flag is
# on. The incident this stages is a partial one - an elevated error rate against
# a shop still mostly working, which is what a canary release looks like when it
# goes wrong. A flag that broke every request would be a different and far
# easier incident to diagnose.
CANARY_SHARE = 0.4


@dataclass(frozen=True)
class Purchase:
    price_cents: int
    in_current_month: bool


@dataclass(frozen=True)
class Account:
    """One shopper's purchase history, as the account page needs it."""

    purchases: tuple[Purchase, ...]
    total_cents: int
    total_this_month_cents: int


def average_spend_per_item(account: Account) -> int:
    """Average spend per item across everything this account has ever bought."""
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
    boundary, where there is a response to turn a failure into and a log to
    record it in - a page that swallowed its own errors would render a wrong
    number instead of an error, which is a worse incident than the one it hid.
    """
    if use_monthly_summary:
        return average_spend_per_item_this_month(account)

    return average_spend_per_item(account)
