"""One-off repair for the monthly totals the derived write path left behind.

NOT RUN. Running it is a person's decision, not this file's: it rewrites stored
account records, which is irreversible. Importing this module does nothing, and
there is no store wired into it - whoever runs it passes a loader and a saver.

What went wrong, and what this puts right: between the deploy on 2026-09-27 and
the fix in `io_shop.purchase_ledger`, purchases written through
`record_purchase_deriving_the_month` did not move `total_this_month_cents`, so
every account written that way carries a monthly figure frozen at whatever it was
when that path first touched it. The purchases themselves were written correctly,
so they are the truth and the total is the copy that drifted. This re-sums the
current month's purchases on each account and writes that figure back.

It repairs the monthly total only. The lifetime total was still being added to
correctly by the broken path, and an account record may carry a trimmed history,
so re-deriving `total_cents` from the purchases on hand could replace a right
answer with a wrong one.

Run it once, like this, against the real store:

    from scripts.repair_monthly_totals import main

    report = main(load_accounts, save_account, dry_run=True)   # look first
    report = main(load_accounts, save_account, dry_run=False)  # then commit

`dry_run=True` works out every repair and saves nothing, so the report can be
read against `io_shop.spend_reconciliation`'s finding before anything is written.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from io_shop.accounts import Account


@dataclass(frozen=True)
class Repair:
    """One account's monthly total, as it was stored and as the purchases say."""

    shopper_id: str
    stored_cents: int
    corrected_cents: int

    @property
    def change_cents(self) -> int:
        return self.corrected_cents - self.stored_cents


def months_purchases_come_to(account: Account) -> int:
    """What this account's current-month purchases actually add up to."""
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def corrected(account: Account) -> Account:
    """The same account with its monthly total re-summed from its purchases."""
    return replace(account, total_this_month_cents=months_purchases_come_to(account))


def needs_repair(account: Account) -> bool:
    return months_purchases_come_to(account) != account.total_this_month_cents


def main(load_accounts: Callable[[], Iterable[Account]],
         save_account: Callable[[Account], None],
         dry_run: bool = True) -> tuple[Repair, ...]:
    """Repair every account whose stored monthly total disagrees with its
    purchases, and report what was changed.

    Accounts that already agree are left alone and not written, so a re-run after
    a successful run reports nothing and touches nothing.
    """
    repairs = []

    for account in load_accounts():
        if not needs_repair(account):
            continue

        put_right = corrected(account)
        repairs.append(
            Repair(
                shopper_id=account.shopper_id,
                stored_cents=account.total_this_month_cents,
                corrected_cents=put_right.total_this_month_cents,
            )
        )

        if not dry_run:
            save_account(put_right)

    return tuple(repairs)
