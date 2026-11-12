"""One-off repair: put right the monthly totals the derive-the-month path left short.

While `monthly-spend-feature` was on, `record_purchase_deriving_the_month` in
`io_shop.purchase_ledger` wrote purchases into the history without moving
`total_this_month_cents`, so stored month totals fell behind their purchases. The
write path is fixed; the totals it already wrote are not, and this corrects them.

NOT RUN. A person has to run this, against the shop's store, deliberately. It
rewrites stored data, which is irreversible.

What it touches, and what it does not:

- Only accounts whose stored month total is *below* what this month's purchases
  come to - the shape this fault left. Those totals are set to the sum of this
  month's purchases.
- An account whose total is *above* its purchases is a different fault and is
  reported, not rewritten.
- Only the current month. Purchases carry a this-month flag and no date, so a
  month that has already closed cannot be recomputed from these records; if the
  month has turned since the fault, those totals need checking separately.

The store is not in this repository, so the caller supplies how accounts are
read and how one is written back. It is a dry run unless `apply=True`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from io_shop.accounts import Account


@dataclass(frozen=True)
class Correction:
    """One account's stored month total, and what it is being set to."""

    shopper_id: str
    stored_cents: int
    corrected_cents: int

    @property
    def short_by_cents(self) -> int:
        return self.corrected_cents - self.stored_cents


@dataclass(frozen=True)
class RepairPlan:
    """What a run found and what it would write (or wrote, where applied)."""

    accounts_checked: int
    to_write: tuple[Account, ...]
    corrections: tuple[Correction, ...]
    left_for_a_person: tuple[Correction, ...]
    applied: bool


def bought_this_month(account: Account) -> int:
    """What this month's purchases come to - the figure the total should hold."""
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def plan_repair(accounts: Iterable[Account]) -> RepairPlan:
    """Which accounts are short, and the account each should be written back as."""
    checked = 0
    to_write: list[Account] = []
    corrections: list[Correction] = []
    left: list[Correction] = []

    for account in accounts:
        checked += 1
        should_be = bought_this_month(account)
        stored = account.total_this_month_cents

        if should_be == stored:
            continue

        correction = Correction(
            shopper_id=account.shopper_id,
            stored_cents=stored,
            corrected_cents=should_be,
        )

        if should_be > stored:
            to_write.append(replace(account, total_this_month_cents=should_be))
            corrections.append(correction)
        else:
            left.append(correction)

    return RepairPlan(
        accounts_checked=checked,
        to_write=tuple(to_write),
        corrections=tuple(corrections),
        left_for_a_person=tuple(left),
        applied=False,
    )


def run(load_accounts: Callable[[], Iterable[Account]],
        save_account: Callable[[Account], None],
        apply: bool = False,
        report: Callable[[str], None] = print) -> RepairPlan:
    """Plan the repair, report it, and write it back only if `apply` is set."""
    plan = plan_repair(load_accounts())

    for correction in plan.corrections:
        report(
            f"{correction.shopper_id}: month total {correction.stored_cents} -> "
            f"{correction.corrected_cents} (short by {correction.short_by_cents})"
        )

    for correction in plan.left_for_a_person:
        report(
            f"{correction.shopper_id}: month total {correction.stored_cents} is ABOVE "
            f"purchases {correction.corrected_cents} - not this fault, left alone"
        )

    report(
        f"checked {plan.accounts_checked}, short {len(plan.corrections)}, "
        f"left for a person {len(plan.left_for_a_person)}, "
        f"{'APPLYING' if apply else 'dry run - nothing written'}"
    )

    if not apply:
        return plan

    for account in plan.to_write:
        save_account(account)

    return replace(plan, applied=True)
