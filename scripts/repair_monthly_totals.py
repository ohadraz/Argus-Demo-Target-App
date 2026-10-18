"""One-off repair for the monthly totals left short by revision 26f1d7e.

NOT YET RUN. A person has to run this against the production account records,
after the fix to `io_shop.purchase_ledger` is deployed (otherwise new purchases
will drift again straight away).

What it does: reads accounts as JSON lines, runs the shop's own check
(`io_shop.spend_reconciliation.reconcile_monthly_totals`) and, for every account
whose stored `total_this_month_cents` is LOWER than its current-month purchases
add up to (the fault: missed writes), sets the stored total to that sum.
Accounts whose stored total is HIGHER than the purchases are a different fault
(something counted twice) and are reported but left untouched.

Usage:
    python scripts/repair_monthly_totals.py accounts.jsonl            # dry run
    python scripts/repair_monthly_totals.py accounts.jsonl --apply \
        --output repaired.jsonl                                      # write

Each input line is an account:
    {"shopper_id": "...", "total_cents": 0, "total_this_month_cents": 0,
     "purchases": [{"price_cents": 0, "in_current_month": true, ...}, ...]}
Every field is written back exactly as read except `total_this_month_cents`.
Re-running it on its own output changes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals

_PURCHASE_FIELDS = (
    "price_cents",
    "in_current_month",
    "category",
    "refunded_cents",
    "delivery_cents",
    "discount_cents",
    "instalments_remaining",
)


def _account_from(record: dict[str, Any]) -> Account:
    purchases = []
    for raw in record["purchases"]:
        fields = {name: raw[name] for name in _PURCHASE_FIELDS if name in raw}
        if raw.get("recorded_at"):
            fields["recorded_at"] = datetime.fromisoformat(raw["recorded_at"])
        purchases.append(Purchase(**fields))
    return Account(
        shopper_id=record["shopper_id"],
        purchases=tuple(purchases),
        total_cents=record["total_cents"],
        total_this_month_cents=record["total_this_month_cents"],
    )


def corrections_for(accounts: Iterable[Account]) -> tuple[dict[str, int], list[str]]:
    """The corrected month total for each short account, and the accounts whose
    total is too high (reported, not repaired)."""
    accounts = list(accounts)
    by_id = {account.shopper_id: account for account in accounts}
    finding = reconcile_monthly_totals(accounts)

    corrected: dict[str, int] = {}
    too_high: list[str] = []
    for disagreement in finding.accounts_that_disagree:
        account = by_id[disagreement.shopper_id]
        if disagreement.gap_cents > 0:
            corrected[account.shopper_id] = (
                account.total_this_month_cents + disagreement.gap_cents
            )
        else:
            too_high.append(account.shopper_id)
    return corrected, too_high


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="accounts as JSON lines")
    parser.add_argument("--apply", action="store_true",
                        help="write corrected records (default: dry run)")
    parser.add_argument("--output", help="where corrected JSON lines go")
    args = parser.parse_args(argv)

    with open(args.input, encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]

    corrected, too_high = corrections_for(_account_from(r) for r in records)

    for record in records:
        if record["shopper_id"] in corrected:
            print(
                f"{record['shopper_id']}: total_this_month_cents "
                f"{record['total_this_month_cents']} -> {corrected[record['shopper_id']]}",
                file=sys.stderr,
            )
    for shopper_id in too_high:
        print(f"{shopper_id}: stored total is HIGHER than purchases - "
              f"not repaired, investigate separately", file=sys.stderr)
    print(f"{len(records)} checked, {len(corrected)} to repair, "
          f"{len(too_high)} too high (left alone)", file=sys.stderr)

    if not args.apply:
        print("dry run: nothing written (pass --apply --output FILE)", file=sys.stderr)
        return 0
    if not args.output:
        parser.error("--apply needs --output")

    with open(args.output, "w", encoding="utf-8") as out:
        for record in records:
            if record["shopper_id"] in corrected:
                record = {**record,
                          "total_this_month_cents": corrected[record["shopper_id"]]}
            out.write(json.dumps(record) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
