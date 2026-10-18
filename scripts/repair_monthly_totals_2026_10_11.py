"""One-off repair: rewrite the monthly totals the derive-the-month path left behind.

NOT RUN. A person has to run this, deliberately, after the write-path fix in
`io_shop.purchase_ledger` is deployed (or with `monthly-spend-feature` off), so
that no new drift is written while it runs. Run it before the current month
rolls over: accounts only carry an "in the current month" flag per purchase,
so this month's figure can be rebuilt from the history only while it is still
this month.

What it does: for every account whose stored `total_this_month_cents`
disagrees with the purchases behind it (as `io_shop.spend_reconciliation`
measures it), rewrite that one field to the sum of this month's purchases.
Purchases and the lifetime total are not touched.

The repository has no storage layer, so `load_accounts` and `save_account`
must be wired to the account store by whoever runs this. Dry run by default;
pass `--apply` to write.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from dataclasses import replace

from io_shop.accounts import Account
from io_shop.spend_reconciliation import reconcile_monthly_totals


def load_accounts() -> Iterable[Account]:
    """Every account to check. Wire this to the account store before running."""
    raise NotImplementedError("wire load_accounts to the account store")


def save_account(account: Account) -> None:
    """Write one account's corrected monthly total back. Wire before running."""
    raise NotImplementedError("wire save_account to the account store")


def what_this_month_comes_to(account: Account) -> int:
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def repaired(accounts: Iterable[Account]) -> Iterator[tuple[Account, Account]]:
    """Each disagreeing account, paired with its corrected version."""
    checked = list(accounts)
    disagreeing = {
        found.shopper_id
        for found in reconcile_monthly_totals(checked).accounts_that_disagree
    }

    for account in checked:
        if account.shopper_id in disagreeing:
            yield account, replace(
                account, total_this_month_cents=what_this_month_comes_to(account)
            )


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    count = 0

    for before, after in repaired(load_accounts()):
        count += 1
        print(
            f"{before.shopper_id}: total_this_month_cents "
            f"{before.total_this_month_cents} -> {after.total_this_month_cents}"
        )

        if apply:
            save_account(after)

    print(f"{count} account(s) {'repaired' if apply else 'would be repaired (dry run)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
