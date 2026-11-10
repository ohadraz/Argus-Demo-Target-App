"""One-off repair: put stored monthly totals back in line with their purchases.

Written for the incident in which accounts were written without
`total_this_month_cents` being moved (deploy 26f1d7e2, from 2026-11-03T07:47:32Z).
NOT RUN. A person has to run it, deliberately, against an export of the accounts.

Input: JSON lines on stdin (or --input), one account per line:
  {"shopper_id": ..., "total_cents": ..., "total_this_month_cents": ...,
   "purchases": [{"price_cents": ..., "in_current_month": ..., "recorded_at": ...}, ...]}

The accounts changed are the ones whose shopper ids come back in
`accounts_that_disagree` from `reconcile_monthly_totals`. The only field this
script changes is `total_this_month_cents`, set to the sum of `price_cents` over
the purchases marked `in_current_month`. Every other field of the input line is
passed through untouched.

Dry run by default: prints what it would change. With --apply, writes the
corrected accounts (only those changed) as JSON lines to stdout (or --output),
for loading back into the store. Run it before the month rolls over, since
`in_current_month` is what it sums by.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def _account_from(record: dict) -> Account:
    return Account(
        shopper_id=record["shopper_id"],
        purchases=tuple(
            Purchase(
                price_cents=int(p["price_cents"]),
                in_current_month=bool(p["in_current_month"]),
                recorded_at=(
                    datetime.fromisoformat(p["recorded_at"])
                    if p.get("recorded_at")
                    else None
                ),
            )
            for p in record.get("purchases", [])
        ),
        total_cents=int(record["total_cents"]),
        total_this_month_cents=int(record["total_this_month_cents"]),
    )


def _bought_this_month(account: Account) -> int:
    return sum(p.price_cents for p in account.purchases if p.in_current_month)


def repair(records: list[dict]) -> list[dict]:
    """The input records whose monthly total disagrees, with it corrected."""
    accounts = {r["shopper_id"]: _account_from(r) for r in records}
    found = reconcile_monthly_totals(accounts.values())
    wrong = {d.shopper_id for d in found.accounts_that_disagree}

    print(
        f"checked {found.accounts_checked}, disagreeing {len(wrong)}, "
        f"oldest affected purchase {found.oldest_affected_purchase_at}",
        file=sys.stderr,
    )

    repaired = []
    for record in records:
        if record["shopper_id"] not in wrong:
            continue
        corrected = _bought_this_month(accounts[record["shopper_id"]])
        print(
            f"{record['shopper_id']}: total_this_month_cents "
            f"{record['total_this_month_cents']} -> {corrected}",
            file=sys.stderr,
        )
        repaired.append({**record, "total_this_month_cents": corrected})

    return repaired


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="JSON lines file of accounts (default stdin)")
    parser.add_argument("--output", help="where to write corrected accounts (default stdout)")
    parser.add_argument("--apply", action="store_true",
                        help="emit corrected accounts; without it, only report")
    args = parser.parse_args()

    source = open(args.input, encoding="utf-8") if args.input else sys.stdin
    with source:
        records = [json.loads(line) for line in source if line.strip()]

    repaired = repair(records)

    if not args.apply:
        print(f"dry run: {len(repaired)} account(s) would change", file=sys.stderr)
        return 0

    sink = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    with sink:
        for record in repaired:
            sink.write(json.dumps(record) + "\n")

    print(f"wrote {len(repaired)} corrected account(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
