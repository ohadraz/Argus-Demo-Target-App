"""One-off repair: put back the monthly totals the derive-the-month path left stale.

NOT RUN. A person has to run this, deliberately, against the affected accounts.

While `monthly-spend-feature` was on, purchases written through
`io_shop.purchase_ledger.record_purchase_deriving_the_month` were added to the
history without moving `total_this_month_cents`, so the stored total fell behind
the purchases behind it. This reads account records, passes them to
`io_shop.spend_reconciliation.reconcile_monthly_totals`, and for each account it
reports with a positive gap writes the total back as the sum of that account's
current-month purchase prices.

Only totals that fell *behind* (a positive gap) are repaired - that is the shape
this fault produces. A total that is *above* its purchases is a different fault;
those are reported on stderr and left alone.

Input: JSON lines on stdin, one account per line:
    {"shopper_id": "...", "total_cents": 0, "total_this_month_cents": 0,
     "purchases": [{"price_cents": 0, "in_current_month": true, ...}, ...]}

Output: by default a dry run - what would change is reported on stderr and
nothing is written. With `--apply`, the corrected records (only the ones that
changed) are written to stdout as JSON lines, in the same shape, for loading
back into the store.

    python scripts/repair_monthly_totals.py < accounts.jsonl            # dry run
    python scripts/repair_monthly_totals.py --apply < accounts.jsonl > fixed.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from typing import Any

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def _purchase_from(record: dict[str, Any]) -> Purchase:
    fields = dict(record)
    recorded_at = fields.get("recorded_at")

    if isinstance(recorded_at, str):
        fields["recorded_at"] = datetime.fromisoformat(recorded_at)

    return Purchase(**fields)


def _account_from(record: dict[str, Any]) -> Account:
    return Account(
        shopper_id=record["shopper_id"],
        purchases=tuple(_purchase_from(p) for p in record["purchases"]),
        total_cents=record["total_cents"],
        total_this_month_cents=record["total_this_month_cents"],
    )


def _bought_this_month(account: Account) -> int:
    return sum(p.price_cents for p in account.purchases if p.in_current_month)


def repaired(accounts: list[Account]) -> tuple[list[Account], list[str]]:
    """The accounts whose stored monthly total fell behind, corrected; and the
    shopper ids of any whose total is above its purchases, left alone."""
    by_id = {account.shopper_id: account for account in accounts}
    found = reconcile_monthly_totals(accounts)
    fixed: list[Account] = []
    skipped: list[str] = []

    for disagreeing in found.accounts_that_disagree:
        account = by_id[disagreeing.shopper_id]

        if disagreeing.gap_cents <= 0:
            skipped.append(disagreeing.shopper_id)
            continue

        fixed.append(
            replace(account, total_this_month_cents=_bought_this_month(account))
        )

    return fixed, skipped


def _as_record(account: Account) -> dict[str, Any]:
    return {
        "shopper_id": account.shopper_id,
        "total_cents": account.total_cents,
        "total_this_month_cents": account.total_this_month_cents,
        "purchases": [
            {
                "price_cents": p.price_cents,
                "in_current_month": p.in_current_month,
                "category": p.category,
                "refunded_cents": p.refunded_cents,
                "delivery_cents": p.delivery_cents,
                "discount_cents": p.discount_cents,
                "instalments_remaining": p.instalments_remaining,
                "recorded_at": p.recorded_at.isoformat() if p.recorded_at else None,
            }
            for p in account.purchases
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="write corrected records to stdout (default: dry run, report only)",
    )
    args = parser.parse_args()

    accounts = [_account_from(json.loads(line)) for line in sys.stdin if line.strip()]
    before = {account.shopper_id: account for account in accounts}
    fixed, skipped = repaired(accounts)

    for account in fixed:
        was = before[account.shopper_id].total_this_month_cents
        print(
            f"{account.shopper_id}: total_this_month_cents {was} -> "
            f"{account.total_this_month_cents}",
            file=sys.stderr,
        )

    for shopper_id in skipped:
        print(
            f"{shopper_id}: stored total is above its purchases - not this fault, "
            f"left alone",
            file=sys.stderr,
        )

    print(
        f"checked {len(accounts)}, repairing {len(fixed)}, skipped {len(skipped)}"
        f"{'' if args.apply else ' (dry run, nothing written)'}",
        file=sys.stderr,
    )

    if args.apply:
        for account in fixed:
            print(json.dumps(_as_record(account)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
