"""One-off repair: put back the monthly totals that were written without the
month being moved.

From the deploy of 26f1d7e2 (2026-10-19T09:13:00Z) purchases were recorded
without `total_this_month_cents` being moved. The fix in
`io_shop.purchase_ledger` stops further drift; this corrects what is already
stored.

NOT RUN. A person has to run it, against an export of the affected accounts,
and load what it writes back into the store. Run it before the month rolls over:
`in_current_month` is relative to the month being counted.

Input is a JSON list of accounts:

    [{"shopper_id": ..., "total_cents": ..., "total_this_month_cents": ...,
      "purchases": [{"price_cents": ..., "in_current_month": ...,
                     "recorded_at": "2026-10-19T09:14:00+00:00", ...}, ...]}, ...]

Usage:

    python scripts/repair_monthly_totals.py accounts.json            # report only
    python scripts/repair_monthly_totals.py accounts.json --write out.json

Without --write nothing is written. With it, only the accounts reported as
disagreeing are written out, each with `total_this_month_cents` set to the sum
of its purchases marked `in_current_month`; nothing else about an account is
changed.
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


def _purchase_from(raw: dict[str, Any]) -> Purchase:
    fields = dict(raw)
    recorded_at = fields.get("recorded_at")
    if recorded_at is not None:
        fields["recorded_at"] = datetime.fromisoformat(recorded_at)
    return Purchase(**fields)


def _account_from(raw: dict[str, Any]) -> Account:
    return Account(
        shopper_id=raw["shopper_id"],
        purchases=tuple(_purchase_from(p) for p in raw["purchases"]),
        total_cents=raw["total_cents"],
        total_this_month_cents=raw["total_this_month_cents"],
    )


def _bought_this_month(account: Account) -> int:
    return sum(p.price_cents for p in account.purchases if p.in_current_month)


def repaired_accounts(raw_accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The raw records of every account reported as disagreeing, corrected."""
    accounts = {raw["shopper_id"]: (raw, _account_from(raw)) for raw in raw_accounts}
    finding = reconcile_monthly_totals(account for _, account in accounts.values())

    print(
        f"checked {finding.accounts_checked}, "
        f"disagreeing {len(finding.accounts_that_disagree)}, "
        f"oldest affected purchase {finding.oldest_affected_purchase_at}"
    )

    repaired = []
    for disagreement in finding.accounts_that_disagree:
        raw, account = accounts[disagreement.shopper_id]
        corrected = replace(
            account, total_this_month_cents=_bought_this_month(account)
        )
        print(
            f"{account.shopper_id}: {account.total_this_month_cents} -> "
            f"{corrected.total_this_month_cents} (gap {disagreement.gap_cents})"
        )
        repaired.append(
            {**raw, "total_this_month_cents": corrected.total_this_month_cents}
        )

    return repaired


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accounts", help="JSON export of the accounts to check")
    parser.add_argument(
        "--write", metavar="PATH",
        help="write the corrected accounts here; without it nothing is written"
    )
    args = parser.parse_args(argv)

    with open(args.accounts, encoding="utf-8") as source:
        raw_accounts = json.load(source)

    repaired = repaired_accounts(raw_accounts)

    if args.write is None:
        print("report only: nothing written (pass --write PATH to write)")
        return 0

    with open(args.write, "w", encoding="utf-8") as target:
        json.dump(repaired, target, indent=2)
    print(f"wrote {len(repaired)} corrected accounts to {args.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
