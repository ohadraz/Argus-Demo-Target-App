"""One-off repair: put back the monthly totals that fell behind their purchases.

NOT YET RUN. A person has to run this, deliberately, against an export of the
accounts. It rewrites stored data, which `io_shop.spend_reconciliation` refuses to
do on purpose; this script is the explicit, asked-for exception.

What it fixes: accounts whose `total_this_month_cents` is LOWER than the sum of
their current-month purchases - the signature of writes that added a purchase
and did not move the month's total. Those totals are set to the sum of the
current-month purchases. Accounts whose total is HIGHER than their purchases are
a different fault (something counted twice); they are reported and left alone.

Run it inside the month the data describes: `in_current_month` is relative to
that month, and a recomputation after the month has turned would be wrong.

Input format (an assumption - this repository holds no storage layer): a JSON
array of accounts, each with `shopper_id`, `total_cents`,
`total_this_month_cents` and `purchases`, each purchase carrying the fields of
`io_shop.accounts.Purchase` (`recorded_at` as ISO-8601 or null).

Usage:
    python scripts/repair_monthly_totals.py accounts.json            # report only
    python scripts/repair_monthly_totals.py accounts.json --apply -o repaired.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def _purchase_from(raw: dict) -> Purchase:
    fields = dict(raw)
    recorded_at = fields.get("recorded_at")
    fields["recorded_at"] = (
        datetime.fromisoformat(recorded_at) if recorded_at else None
    )
    return Purchase(**fields)


def _account_from(raw: dict) -> Account:
    return Account(
        shopper_id=raw["shopper_id"],
        purchases=tuple(_purchase_from(p) for p in raw["purchases"]),
        total_cents=raw["total_cents"],
        total_this_month_cents=raw["total_this_month_cents"],
    )


def _account_to(account: Account) -> dict:
    raw = asdict(account)
    raw["purchases"] = [
        {**p, "recorded_at": p["recorded_at"].isoformat() if p["recorded_at"] else None}
        for p in raw["purchases"]
    ]
    return raw


def _bought_this_month(account: Account) -> int:
    return sum(p.price_cents for p in account.purchases if p.in_current_month)


def repair(accounts: list[Account]) -> tuple[list[Account], list[str], list[str]]:
    """The accounts with fallen-behind month totals corrected, plus which
    shoppers were repaired and which were left alone for a human to look at."""
    finding = reconcile_monthly_totals(accounts)
    behind = {d.shopper_id for d in finding.accounts_that_disagree if d.gap_cents > 0}
    ahead = [d.shopper_id for d in finding.accounts_that_disagree if d.gap_cents < 0]

    repaired = [
        replace(account, total_this_month_cents=_bought_this_month(account))
        if account.shopper_id in behind
        else account
        for account in accounts
    ]
    return repaired, sorted(behind), ahead


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accounts_json")
    parser.add_argument("--apply", action="store_true",
                        help="write the repaired accounts (default: report only)")
    parser.add_argument("-o", "--output", help="where to write repaired accounts")
    args = parser.parse_args(argv)

    with open(args.accounts_json) as handle:
        accounts = [_account_from(raw) for raw in json.load(handle)]

    finding = reconcile_monthly_totals(accounts)
    repaired, fixed, left_alone = repair(accounts)

    print(f"checked {finding.accounts_checked} accounts; "
          f"{len(fixed)} month totals behind their purchases; "
          f"{len(left_alone)} ahead of them (not touched)")
    print(f"oldest affected purchase: {finding.oldest_affected_purchase_at}")
    for before, after in zip(accounts, repaired):
        if before.shopper_id in fixed:
            print(f"  {before.shopper_id}: {before.total_this_month_cents} -> "
                  f"{after.total_this_month_cents}")
    for shopper_id in left_alone:
        print(f"  {shopper_id}: total exceeds purchases - needs a human", file=sys.stderr)

    if not args.apply:
        print("report only; re-run with --apply -o FILE to write the repair")
        return 0

    if not args.output:
        parser.error("--apply needs -o/--output")

    with open(args.output, "w") as handle:
        json.dump([_account_to(a) for a in repaired], handle, indent=2)
    print(f"wrote {len(repaired)} accounts to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
