"""One-off repair: put back the monthly totals the derive-the-month path left behind.

NOT YET RUN. A person has to run this, deliberately, after reading its dry-run
report.

From the monthly-spend-feature rollout (2026-10-11T16:22Z) until the fix in
`io_shop.purchase_ledger`, purchases recorded through
`record_purchase_deriving_the_month` were appended to the history without
moving `total_this_month_cents`. Every account written that way holds a stored
monthly total *below* what its in-month purchases come to.

This script uses the shop's own check, `reconcile_monthly_totals`, to find
those accounts and sets each one's stored monthly total to the sum of its
in-month purchases. It only corrects totals that have fallen behind (positive
gap), which is the shape this fault leaves. Totals that are too high would be a
different fault: they are listed in the report and left alone.

Input: a JSON file holding a list of accounts exported from the store, each
shaped like:

    {"shopper_id": "...", "total_cents": 0, "total_this_month_cents": 0,
     "purchases": [{"price_cents": 0, "in_current_month": true,
                    "recorded_at": "2026-10-11T16:22:00+00:00", ...}, ...]}

Usage:

    PYTHONPATH=src python scripts/repair_monthly_totals.py accounts.json
        # dry run: reports what would change, writes nothing

    PYTHONPATH=src python scripts/repair_monthly_totals.py accounts.json \
        --apply --output repaired.json
        # writes every repaired account (and only those) to repaired.json
        # for loading back into the store; the input file is never modified

Loading the corrected totals back into the store is up to the operator. Only
`total_this_month_cents` changes; nothing else about an account is touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def _purchase_from(raw: dict[str, Any]) -> Purchase:
    recorded_at = raw.get("recorded_at")
    return Purchase(
        price_cents=int(raw["price_cents"]),
        in_current_month=bool(raw["in_current_month"]),
        category=raw.get("category", "General"),
        refunded_cents=int(raw.get("refunded_cents", 0)),
        delivery_cents=int(raw.get("delivery_cents", 0)),
        discount_cents=int(raw.get("discount_cents", 0)),
        instalments_remaining=int(raw.get("instalments_remaining", 0)),
        recorded_at=datetime.fromisoformat(recorded_at) if recorded_at else None,
    )


def _account_from(raw: dict[str, Any]) -> Account:
    return Account(
        shopper_id=str(raw["shopper_id"]),
        purchases=tuple(_purchase_from(p) for p in raw["purchases"]),
        total_cents=int(raw["total_cents"]),
        total_this_month_cents=int(raw["total_this_month_cents"]),
    )


def _bought_this_month(account: Account) -> int:
    return sum(p.price_cents for p in account.purchases if p.in_current_month)


def repair(accounts: list[Account]) -> tuple[list[Account], list[str]]:
    """The repaired accounts, and a report line for every disagreement found."""
    finding = reconcile_monthly_totals(accounts)
    by_id = {account.shopper_id: account for account in accounts}
    repaired: list[Account] = []
    report = [
        f"checked {finding.accounts_checked} accounts, "
        f"{len(finding.accounts_that_disagree)} disagree"
    ]

    for disagreement in finding.accounts_that_disagree:
        account = by_id[disagreement.shopper_id]

        if disagreement.gap_cents <= 0:
            report.append(
                f"SKIPPED {account.shopper_id}: stored total "
                f"{account.total_this_month_cents} is ABOVE purchases "
                f"{_bought_this_month(account)} - not this fault, left alone"
            )
            continue

        corrected = _bought_this_month(account)
        repaired.append(replace(account, total_this_month_cents=corrected))
        report.append(
            f"REPAIR {account.shopper_id}: {account.total_this_month_cents} -> "
            f"{corrected} (gap {disagreement.gap_cents}, oldest affected "
            f"purchase {disagreement.oldest_affected_purchase_at})"
        )

    return repaired, report


def _as_json(account: Account) -> dict[str, Any]:
    return {
        "shopper_id": account.shopper_id,
        "total_cents": account.total_cents,
        "total_this_month_cents": account.total_this_month_cents,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accounts", type=Path, help="JSON list of accounts")
    parser.add_argument("--apply", action="store_true",
                        help="write the repaired totals (default: dry run)")
    parser.add_argument("--output", type=Path,
                        help="where --apply writes repaired accounts")
    args = parser.parse_args(argv)

    if args.apply and args.output is None:
        parser.error("--apply needs --output")
    if args.apply and args.output.resolve() == args.accounts.resolve():
        parser.error("--output must not overwrite the input")

    raw = json.loads(args.accounts.read_text())
    accounts = [_account_from(entry) for entry in raw]
    repaired, report = repair(accounts)

    for line in report:
        print(line)

    if not args.apply:
        print(f"DRY RUN: {len(repaired)} accounts would be repaired; nothing written")
        return 0

    args.output.write_text(json.dumps([_as_json(a) for a in repaired], indent=2))
    print(f"wrote {len(repaired)} repaired accounts to {args.output}")

    still_wrong = reconcile_monthly_totals(
        [next((r for r in repaired if r.shopper_id == a.shopper_id), a)
         for a in accounts]
    )
    print(f"after repair, {len(still_wrong.accounts_that_disagree)} accounts "
          f"still disagree (only the skipped ones should remain)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
