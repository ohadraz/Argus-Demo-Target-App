"""One-off repair: rewrite stored monthly totals that disagree with purchases.

Written for the incident in which purchases were recorded without the month's
total being moved (revision 26f1d7e2c82ce2abff8f9b6424dc226f4f37fed2, first
wrong record at 2026-10-29T00:58:00Z). The code fix stops new wrong totals; this
puts right the ones already written.

NOT RUN. A person has to run it, deliberately, against an export of the affected
accounts, and then write the corrected accounts back to the store.

Usage:

    python -m io_shop.repair_monthly_totals accounts.json            # report only
    python -m io_shop.repair_monthly_totals accounts.json --apply \
        --output corrected.json                                     # write file

`accounts.json` is a JSON list of accounts, each
`{"shopper_id", "total_cents", "total_this_month_cents", "purchases": [...]}`
with purchases carrying at least `price_cents` and `in_current_month`, and
optionally `recorded_at` as an ISO-8601 instant. `in_current_month` is whatever
the export decided, so take the export in the month the repair is run.

Which accounts are corrected is whatever `reconcile_monthly_totals` reports as
disagreeing. The corrected figure is what the account's current-month purchases
add up to. Only `total_this_month_cents` is changed; accounts not reported are
not written at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from typing import Any

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def what_the_month_comes_to(account: Account) -> int:
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def repaired_accounts(accounts: Iterable[Account]) -> list[tuple[Account, Account]]:
    """Each disagreeing account, paired with its corrected version."""
    checked = list(accounts)
    found = reconcile_monthly_totals(checked)
    wrong = {account.shopper_id for account in found.accounts_that_disagree}

    return [
        (account, replace(account, total_this_month_cents=what_the_month_comes_to(account)))
        for account in checked
        if account.shopper_id in wrong
    ]


def _purchase_from(raw: dict[str, Any]) -> Purchase:
    fields = dict(raw)
    if fields.get("recorded_at") is not None:
        fields["recorded_at"] = datetime.fromisoformat(fields["recorded_at"])
    return Purchase(**fields)


def _account_from(raw: dict[str, Any]) -> Account:
    return Account(
        shopper_id=raw["shopper_id"],
        purchases=tuple(_purchase_from(each) for each in raw["purchases"]),
        total_cents=raw["total_cents"],
        total_this_month_cents=raw["total_this_month_cents"],
    )


def _account_to(account: Account) -> dict[str, Any]:
    return {
        "shopper_id": account.shopper_id,
        "total_cents": account.total_cents,
        "total_this_month_cents": account.total_this_month_cents,
        "purchases": [
            {
                "price_cents": purchase.price_cents,
                "in_current_month": purchase.in_current_month,
                "category": purchase.category,
                "refunded_cents": purchase.refunded_cents,
                "delivery_cents": purchase.delivery_cents,
                "discount_cents": purchase.discount_cents,
                "instalments_remaining": purchase.instalments_remaining,
                "recorded_at": (
                    purchase.recorded_at.isoformat()
                    if purchase.recorded_at is not None
                    else None
                ),
            }
            for purchase in account.purchases
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accounts", help="JSON file of exported accounts")
    parser.add_argument("--apply", action="store_true",
                        help="write corrected accounts to --output")
    parser.add_argument("--output", help="where to write corrected accounts")
    args = parser.parse_args(argv)

    if args.apply and not args.output:
        parser.error("--apply needs --output")

    with open(args.accounts, encoding="utf-8") as handle:
        accounts = [_account_from(raw) for raw in json.load(handle)]

    found = reconcile_monthly_totals(accounts)
    oldest = {
        each.shopper_id: each.oldest_affected_purchase_at
        for each in found.accounts_that_disagree
    }
    repairs = repaired_accounts(accounts)

    for before, after in repairs:
        print(
            f"{before.shopper_id}: total_this_month_cents "
            f"{before.total_this_month_cents} -> {after.total_this_month_cents} "
            f"(oldest affected purchase: {oldest.get(before.shopper_id)})"
        )

    print(f"{len(repairs)} of {len(accounts)} accounts need correcting.")

    if not args.apply:
        print("Report only; nothing written. Re-run with --apply --output FILE.")
        return 0

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump([_account_to(after) for _, after in repairs], handle, indent=2)

    print(f"Corrected accounts written to {args.output}; load them into the store.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
