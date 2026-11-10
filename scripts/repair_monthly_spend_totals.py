"""One-off repair: put right the monthly spend totals the deriving write path left short.

While monthly-spend-feature was on (from 2026-10-19T08:52:00Z), purchases
recorded through `record_purchase_deriving_the_month` did not move
`total_this_month_cents`, so the stored total fell short of the purchases behind
it. This script picks out every account that
`io_shop.spend_reconciliation.reconcile_monthly_totals` reports as disagreeing,
and sets its total to what its purchases in the current month add up to.

NOT RUN. A person has to run this, deliberately, against an export of the
accounts. It rewrites stored figures, which cannot be undone, so:

  * by default it only reports what it would change;
  * it writes only with `--write OUTPUT`, and only to that output file - the
    input is never modified, and loading the output back into the store is a
    separate, human step.

Input: JSON lines, one account per line, in the shape of
`io_shop.accounts.Account`:

  {"shopper_id": "...", "total_cents": 0, "total_this_month_cents": 0,
   "purchases": [{"price_cents": 0, "in_current_month": true, ...}, ...]}

Purchase fields other than `price_cents` and `in_current_month` are carried
through unchanged; `recorded_at`, where present, is an ISO-8601 string.

Usage:
  python scripts/repair_monthly_spend_totals.py accounts.jsonl
  python scripts/repair_monthly_spend_totals.py accounts.jsonl --write repaired.jsonl
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


def what_the_month_comes_to(account: Account) -> int:
    """This month's purchases added up - the figure the stored total should hold."""
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def repaired(accounts: list[Account]) -> tuple[list[Account], list[tuple[str, int, int]]]:
    """Every account, with the disagreeing ones corrected, and what was changed.

    Accounts that already reconcile are returned untouched. The change list is
    (shopper_id, stored total before, total after) for each corrected account.
    """
    disagreeing = {
        found.shopper_id
        for found in reconcile_monthly_totals(accounts).accounts_that_disagree
    }
    out: list[Account] = []
    changes: list[tuple[str, int, int]] = []

    for account in accounts:
        if account.shopper_id not in disagreeing:
            out.append(account)
            continue

        corrected = what_the_month_comes_to(account)
        changes.append((account.shopper_id, account.total_this_month_cents, corrected))
        out.append(replace(account, total_this_month_cents=corrected))

    return out, changes


def _purchase_from(raw: dict[str, Any]) -> Purchase:
    fields = dict(raw)
    if fields.get("recorded_at") is not None:
        fields["recorded_at"] = datetime.fromisoformat(fields["recorded_at"])
    return Purchase(**fields)


def _account_from(raw: dict[str, Any]) -> Account:
    return Account(
        shopper_id=raw["shopper_id"],
        purchases=tuple(_purchase_from(purchase) for purchase in raw["purchases"]),
        total_cents=raw["total_cents"],
        total_this_month_cents=raw["total_this_month_cents"],
    )


def _purchase_as_json(purchase: Purchase) -> dict[str, Any]:
    as_dict = dict(purchase.__dict__)
    if as_dict.get("recorded_at") is not None:
        as_dict["recorded_at"] = as_dict["recorded_at"].isoformat()
    return as_dict


def _account_as_json(account: Account) -> dict[str, Any]:
    return {
        "shopper_id": account.shopper_id,
        "purchases": [_purchase_as_json(purchase) for purchase in account.purchases],
        "total_cents": account.total_cents,
        "total_this_month_cents": account.total_this_month_cents,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accounts", help="JSON-lines export of the accounts")
    parser.add_argument(
        "--write",
        metavar="OUTPUT",
        help="write every account, corrected, to this file (default: report only)",
    )
    args = parser.parse_args(argv)

    with open(args.accounts, encoding="utf-8") as source:
        accounts = [_account_from(json.loads(line)) for line in source if line.strip()]

    fixed, changes = repaired(accounts)

    for shopper_id, before, after in changes:
        print(f"{shopper_id}: total_this_month_cents {before} -> {after} "
              f"(short by {after - before})")

    print(f"{len(changes)} of {len(accounts)} accounts need correcting")

    if args.write is None:
        print("dry run: nothing written (pass --write OUTPUT to write)")
        return 0

    with open(args.write, "w", encoding="utf-8") as target:
        for account in fixed:
            target.write(json.dumps(_account_as_json(account)) + "\n")

    still_wrong = reconcile_monthly_totals(fixed)
    if still_wrong.anything_disagrees:
        print(f"WARNING: {len(still_wrong.accounts_that_disagree)} accounts still "
              f"disagree after repair", file=sys.stderr)
        return 1

    print(f"wrote {len(fixed)} accounts to {args.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
