"""Asking the purchases whether the cached figures kept up with them.

The companion to `spend_reconciliation`, and the same question asked of a
different copy. There the shop checks the total it stores beside a shopper's
history; here it checks the one it keeps in front of that - the rendered figure
the account page reads before it works anything out.

The two find faults that look identical on the page and are opposites
underneath. A stored total that has fallen behind is *authoritative and wrong*:
re-reading it changes nothing, and putting it right means rewriting data. A
cached figure that has fallen behind is *a copy of something still correct*:
the purchases behind it never moved, so discarding the copy is a complete fix
and costs the shop one recomputation. Which copy is lying decides what anybody
should do about it, and nothing else in the finding does.

Nothing here reads a flag, discards anything or writes anything back. It answers
one question about a set of accounts and what the cache holds for them, and the
answer is a value.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from io_shop.accounts import Account
from io_shop.summary_cache import SummaryEntry


@dataclass(frozen=True)
class StaleSummary:
    """One shopper whose cached figure is not what their purchases come to.

    `gap_cents` is the purchases minus what the cache holds, so a positive figure
    is a cache that has fallen behind. Signed for the reason the stored total's
    is: a cached figure that is low has missed purchases, and one that is high is
    counting something the ledger does not have - which is a different fault with
    a different cause.

    `items_short` is the same disagreement in the other field an entry carries.
    An entry holds the figure *and* the number of purchases it was worked out
    over, so a stale one is wrong twice, and the page shows both. It is what
    separates this from a figure that is merely miscomputed: a cache that
    disagreed about the money and agreed about the count would be holding an
    arithmetic error rather than an older answer.

    `oldest_missing_purchase_at` is the earliest purchase the cached figure does
    not account for. It dates the copy rather than the incident - it says when
    this entry stopped keeping up, which is when replication to it broke, and not
    when the stale copy was put in front of shoppers. `None` where the gap cannot
    be attributed to any purchase in particular.
    """

    shopper_id: str
    gap_cents: int
    items_short: int
    oldest_missing_purchase_at: datetime | None


@dataclass(frozen=True)
class CacheReconciliation:
    """What one run of the check found in the cache.

    `entries_checked` is carried beside the disagreements for the reason
    `accounts_checked` is: ninety entries out of ninety and ninety out of two
    hundred and forty are the same number and opposite incidents. The first says
    every figure the cache holds is wrong, which is a cache that should be
    emptied; the second says a share of them is, which is a cache that should
    have those entries removed and the rest left alone.
    """

    entries_checked: int
    entries_that_disagree: tuple[StaleSummary, ...]

    @property
    def anything_disagrees(self) -> bool:
        return bool(self.entries_that_disagree)

    @property
    def largest_gap_cents(self) -> int:
        """The widest gap found, or nothing where nothing disagrees."""
        return max(
            (stale.gap_cents for stale in self.entries_that_disagree), default=0
        )

    @property
    def largest_items_short(self) -> int:
        """The most purchases any one cached figure is missing.

        Its own maximum rather than the item count of whichever entry has the
        widest gap, and the two need not belong to the same entry: a shopper who
        bought one expensive thing has the larger gap, and one who bought three
        cheap things is missing more purchases. Anything saying them as one
        entry's figures would be describing an entry that may not exist.
        """
        return max(
            (stale.items_short for stale in self.entries_that_disagree), default=0
        )

    @property
    def oldest_missing_purchase_at(self) -> datetime | None:
        """The earliest purchase any stale figure fails to account for.

        What dates the fault, and the only thing in the finding that does. A
        cached figure froze when whatever feeds it stopped, so the oldest
        purchase missing from any of them is that instant - and it is earlier
        than the moment shoppers began reading stale figures, which no amount of
        reading the cache can recover.

        `None` where no disagreement could be attributed to a purchase.
        """
        dated = [
            stale.oldest_missing_purchase_at
            for stale in self.entries_that_disagree
            if stale.oldest_missing_purchase_at is not None
        ]

        return min(dated) if dated else None


def reconcile_cached_summaries(
    accounts: Iterable[Account], held: Mapping[str, SummaryEntry]
) -> CacheReconciliation:
    """Every cached figure that disagrees with the purchases behind it.

    Only accounts the cache actually holds something for are checked, and a
    shopper it holds nothing for is not a disagreement. A cache with no entry is
    a cache working normally - every entry has a first request, and the page
    recomputes and renders - so counting absences as findings would report a cold
    cache as an incident.

    Keyed by shopper rather than by whatever address the cache stores them under.
    How an entry is addressed is a property of the deployment's cache, not of the
    shop's arithmetic, and a comparison that knew the key format would be a
    comparison that could only be run against one kind of store.
    """
    return CacheReconciliation(
        entries_checked=sum(1 for account in accounts if account.shopper_id in held),
        entries_that_disagree=tuple(
            stale
            for account in accounts
            if (stale := _how_far_behind(account, held.get(account.shopper_id)))
        )
    )


def _how_far_behind(account: Account,
                    entry: SummaryEntry | None) -> StaleSummary | None:
    """How this shopper's cached figure differs from their purchases, if it does.

    `None` both where the cache holds nothing and where what it holds agrees,
    which are different facts with the same consequence here: neither is
    something to report.
    """
    if entry is None:
        return None

    counted = sum(purchase.price_cents for purchase in account.purchases)
    gap = counted - entry.amount_cents
    items_short = len(account.purchases) - entry.items_counted

    if gap == 0 and items_short == 0:
        return None

    return StaleSummary(
        shopper_id=account.shopper_id,
        gap_cents=gap,
        items_short=items_short,
        oldest_missing_purchase_at=_the_oldest_purchase_missing_from(
            account, entry.items_counted
        )
    )


def _the_oldest_purchase_missing_from(account: Account,
                                      counted: int) -> datetime | None:
    """When the earliest purchase this entry does not account for was recorded.

    A frozen entry holds what it held, so the purchases it is missing are the
    newest ones - and the oldest of those is the moment it stopped being updated.
    Read off the position rather than by matching amounts: two purchases of the
    same price are indistinguishable by value and distinct in time, and it is the
    time that dates the fault.

    `None` where the entry is not simply short of purchases - it counts at least
    as many as the account has, so whatever is wrong with it is not that it
    stopped keeping up, and dating it from a purchase would invent a cause.
    """
    recorded = sorted(
        purchase.recorded_at for purchase in account.purchases
        if purchase.recorded_at is not None
    )

    if counted >= len(recorded) or counted < 0:
        return None

    return recorded[counted]
