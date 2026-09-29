"""Asking the purchases whether the totals kept up with them.

A running total is a second copy of something the purchases already say. Copies
drift, and this is the shop's check on the one copy it keeps: re-add what a
shopper bought this month and set it against the figure stored beside their
history.

Nothing here reads a flag, fixes anything or writes anything back. It answers one
question about a set of accounts, and the answer is a value - which is what lets
the same arithmetic be run by the shop's own scheduled job, by a console showing
what it found, and by a test that hands it two accounts.

Repairing what it finds is deliberately not here. A total that has fallen behind
is corrected by rewriting stored data, which is irreversible and is nobody's to
do without being asked; this says how far behind and since when, and stops.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from io_shop.accounts import Account, Purchase


@dataclass(frozen=True)
class DisagreeingAccount:
    """One account whose stored monthly total is not what its purchases come to.

    `gap_cents` is the purchases minus the total, so a positive figure is a total
    that has fallen behind. Signed rather than absolute, because the two
    directions are different faults: a total that is low has missed writes, and a
    total that is high has counted something twice.

    `oldest_affected_purchase_at` is the earliest purchase this account's gap can
    be accounted for by - see `_the_oldest_purchase_behind`. `None` where the
    purchases carry no times to reason from, which is an account whose gap can be
    measured and not dated.
    """

    shopper_id: str
    gap_cents: int
    oldest_affected_purchase_at: datetime | None


@dataclass(frozen=True)
class Reconciliation:
    """What one run of the check found.

    `accounts_checked` is carried beside the disagreements because a count of
    disagreements means nothing without it: forty accounts out of forty thousand
    and forty out of forty are the same number and opposite incidents.

    The three figures below are derived rather than stored, so a reader cannot be
    handed a largest gap that belongs to no account in the list.
    """

    accounts_checked: int
    accounts_that_disagree: tuple[DisagreeingAccount, ...]

    @property
    def anything_disagrees(self) -> bool:
        return bool(self.accounts_that_disagree)

    @property
    def largest_gap_cents(self) -> int:
        """The widest gap found, or nothing where nothing disagrees."""
        return max(
            (account.gap_cents for account in self.accounts_that_disagree), default=0
        )

    @property
    def oldest_affected_purchase_at(self) -> datetime | None:
        """The earliest purchase any of these gaps can be accounted for by.

        The closest thing the check has to a date for the fault, and the only
        thing in the whole finding that has one: a check that runs weekly says
        nothing about when the writing went wrong by the fact of having run.

        `None` where no disagreeing account carried a time, which leaves a
        finding that can be reported and not dated.
        """
        dated = [
            account.oldest_affected_purchase_at
            for account in self.accounts_that_disagree
            if account.oldest_affected_purchase_at is not None
        ]

        return min(dated) if dated else None


def reconcile_monthly_totals(accounts: Iterable[Account]) -> Reconciliation:
    """Every account whose stored monthly total disagrees with its purchases.

    Takes the accounts rather than fetching them, for the reason every other
    module in this shop is handed what it works on: whoever is running the check
    decides which accounts it covers - all of them on the shop's own schedule,
    two of them in a test - and a function that went looking would be one that
    could only ever be run one way.
    """
    checked = list(accounts)

    return Reconciliation(
        accounts_checked=len(checked),
        accounts_that_disagree=tuple(
            _the_disagreement_in(account)
            for account in checked
            if _bought_this_month(account) != account.total_this_month_cents
        )
    )


def _the_disagreement_in(account: Account) -> DisagreeingAccount:
    gap = _bought_this_month(account) - account.total_this_month_cents

    return DisagreeingAccount(
        shopper_id=account.shopper_id,
        gap_cents=gap,
        oldest_affected_purchase_at=_the_oldest_purchase_behind(gap, account)
    )


def _bought_this_month(account: Account) -> int:
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def _the_oldest_purchase_behind(gap_cents: int, account: Account) -> datetime | None:
    """The earliest purchase this gap can be accounted for by.

    Attributed rather than looked up, because nothing in a purchase records
    whether it reached the total. What is known is how far behind the total is,
    and that a total only ever falls further behind - so the purchases the gap is
    made of are the most recent ones, and the oldest of them is where it began.

    Taken newest-first until the prices come to the gap. The last one taken is
    the answer: every purchase older than it is accounted for by the total, and
    every purchase from it onwards is not.

    A gap that is not a sum of purchases at all - one wider than the month, or a
    total that is somehow too high - dates nothing, and says so. Guessing at a
    purchase there would put a date on the alert that the data does not support,
    which is worse than an alert with no date in it.
    """
    if gap_cents <= 0:
        return None

    unaccounted = 0

    for purchase in reversed(_this_months_purchases_in(account)):
        unaccounted += purchase.price_cents

        if unaccounted >= gap_cents:
            return purchase.recorded_at

    return None


def _this_months_purchases_in(account: Account) -> list[Purchase]:
    """This month's purchases, in the order the shop wrote them down.

    The stored order rather than a sort by time, because that is the order the
    total was added up in and the order a gap accumulated in - and because a
    purchase whose time was never recorded still has a place in the history,
    where it has nothing to be sorted by.
    """
    return [
        purchase for purchase in account.purchases if purchase.in_current_month
    ]
