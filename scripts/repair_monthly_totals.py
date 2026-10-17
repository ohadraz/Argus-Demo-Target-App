"""One-off repair: rewrite stored monthly totals that fell behind their purchases.

While `monthly-spend-feature` was on (from 2026-10-10T20:00:22Z), purchases
recorded through `record_purchase_deriving_the_month` were added to the
history but not to `Account.total_this_month_cents`. The code fault is fixed in
`io_shop.purchase_ledger`; this script repairs the totals already written.

NOT YET RUN. A person has to run it, deliberately:

    python scripts/repair_monthly_totals.py            # dry run: report only
    python scripts/repair_monthly_totals.py --apply    # write corrected totals

The repair sets each disagreeing account's `total_this_month_cents` to the sum
of its current-month purchases. That is the same arithmetic
`io_shop.spend_reconciliation` uses to find the gap, so after the repair the
reconciliation reports nothing. Accounts that already agree are left alone.

This repository has no storage layer, so `load_accounts` and `save_account`
below are the two places whoever runs this must connect to the real account
store. Until they do, the script refuses to run rather than pretending.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Iterator
from dataclasses import replace

from io_shop.accounts import Account
from io_shop.spend_reconciliation import reconcile_monthly_totals


def bought_this_month(account: Account) -> int:
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def repaired_accounts(accounts: Iterable[Account]) -> Iterator[tuple[Account, Account]]:
    """(before, after) for every account whose stored monthly total disagrees."""
    checked = list(accounts)
    disagreeing = {
        found.shopper_id
        for found in reconcile_monthly_totals(checked).accounts_that_disagree
    }

    for account in checked:
        if account.shopper_id not in disagreeing:
            continue

        yield account, replace(
            account, total_this_month_cents=bought_this_month(account)
        )


def load_accounts() -> Iterable[Account]:
    """Every account to check. Connect this to the real account store."""
    raise NotImplementedError(
        "connect load_accounts to the account store before running this repair"
    )


def save_account(account: Account) -> None:
    """Write one corrected account back. Connect this to the real account store."""
    raise NotImplementedError(
        "connect save_account to the account store before running this repair"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="write corrected totals back (default: report only)"
    )
    arguments = parser.parse_args(argv)

    repaired = 0

    for before, after in repaired_accounts(load_accounts()):
        gap = after.total_this_month_cents - before.total_this_month_cents
        print(
            f"{before.shopper_id}: stored {before.total_this_month_cents} -> "
            f"{after.total_this_month_cents} (gap {gap})"
        )

        if arguments.apply:
            save_account(after)

        repaired += 1

    verb = "repaired" if arguments.apply else "would repair"
    print(f"{verb} {repaired} account(s)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
