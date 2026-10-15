"""One-off repair: put back the monthly totals deploy 26f1d7e2 left behind.

NOT RUN. A person has to run this, deliberately, against the account store.

From 2026-10-05T22:35 the write path selected by the derive-the-month rollout
stopped adding to `total_this_month_cents`, so every account written through it
holds a month figure lower than its purchases say. The purchases themselves were
written correctly (and so was `total_cents`, which both paths always moved), so
the right month figure is exactly what `io_shop.spend_reconciliation` re-adds:
the sum of this month's purchase prices.

This touches only the accounts the reconciliation says disagree, rewrites only
`total_this_month_cents`, and reports each change. By default it is a dry run;
`--apply` is needed to write anything back. Wiring `load_accounts` and
`save_account` to the real store is left to whoever runs it, because rewriting
stored data is irreversible and is nobody's to do without being asked.

Run it only after the fix is deployed, otherwise new writes keep drifting.
The month boundary matters: run it within the same month the drift happened in,
since `in_current_month` is what the totals are re-added from.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable
from dataclasses import replace

from io_shop.accounts import Account
from io_shop.spend_reconciliation import reconcile_monthly_totals


def what_the_month_should_be(account: Account) -> int:
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def repaired_accounts(accounts: Iterable[Account]) -> list[tuple[Account, Account]]:
    """(before, after) for every account whose month total disagrees.

    Only accounts the shop's own check flags are touched. A total that is
    *higher* than its purchases is not what this fault produces, so it is
    reported and left alone for a person to look at.
    """
    checked = list(accounts)
    finding = reconcile_monthly_totals(checked)
    disagreeing = {d.shopper_id: d for d in finding.accounts_that_disagree}

    repairs = []
    for account in checked:
        disagreement = disagreeing.get(account.shopper_id)
        if disagreement is None:
            continue
        if disagreement.gap_cents < 0:
            print(
                f"SKIP {account.shopper_id}: stored month total is higher than its "
                f"purchases by {-disagreement.gap_cents} cents - not this fault",
                file=sys.stderr,
            )
            continue
        repairs.append((
            account,
            replace(account, total_this_month_cents=what_the_month_should_be(account)),
        ))

    return repairs


def run(load_accounts: Callable[[], Iterable[Account]],
        save_account: Callable[[Account], None],
        apply: bool) -> int:
    repairs = repaired_accounts(load_accounts())

    for before, after in repairs:
        print(
            f"{'FIX' if apply else 'WOULD FIX'} {before.shopper_id}: "
            f"total_this_month_cents {before.total_this_month_cents} -> "
            f"{after.total_this_month_cents}"
        )
        if apply:
            save_account(after)

    print(f"{len(repairs)} account(s) {'repaired' if apply else 'to repair'}")
    return len(repairs)


def load_accounts() -> Iterable[Account]:
    raise NotImplementedError("wire this to the account store before running")


def save_account(account: Account) -> None:
    raise NotImplementedError("wire this to the account store before running")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="write the repaired totals back (default: dry run)")
    arguments = parser.parse_args()
    run(load_accounts, save_account, apply=arguments.apply)
