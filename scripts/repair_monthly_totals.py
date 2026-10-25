"""One-off repair for monthly totals left behind by the monthly-spend-feature write path.

NOT YET RUN. A person has to run this, deliberately, against the real store.

While `monthly-spend-feature` was on (from 2026-10-18T18:52:00Z), purchases were
recorded through `record_purchase_deriving_the_month`, which moved the lifetime
total but not `total_this_month_cents`. An affected account's stored month total
is therefore below what its this-month purchases add up to, by the price of each
purchase written that way. The lifetime total was moved and is not touched here.

What this script does:
  * reads accounts as JSON lines on stdin (one account per line, fields as in
    `io_shop.accounts.Account` / `Purchase`; `recorded_at` as ISO-8601 or null);
  * passes them to `io_shop.spend_reconciliation.reconcile_monthly_totals`;
  * for each account reported with a positive gap (total too low), sets
    `total_this_month_cents` to the sum of this month's purchase prices;
  * leaves accounts reported with a negative gap (total too high) untouched and
    lists them, since that is not the shape this fault produces;
  * with `--apply`, writes every account back out as JSON lines on stdout
    (unchanged ones unchanged); always writes a report on stderr.

Dry run by default: without `--apply`, nothing is written to stdout. Exporting
the accounts from the store in this shape and writing the output back is the
operator's job.

Usage:
    python scripts/repair_monthly_totals.py < accounts.jsonl            # report only
    python scripts/repair_monthly_totals.py --apply < accounts.jsonl > repaired.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def _purchase_from(raw: dict[str, Any]) -> Purchase:
    recorded_at = raw.get("recorded_at")
    fields = dict(raw)
    fields["recorded_at"] = (
        datetime.fromisoformat(recorded_at) if recorded_at is not None else None
    )
    return Purchase(**fields)


def _account_from(raw: dict[str, Any]) -> Account:
    return Account(
        shopper_id=raw["shopper_id"],
        purchases=tuple(_purchase_from(purchase) for purchase in raw["purchases"]),
        total_cents=raw["total_cents"],
        total_this_month_cents=raw["total_this_month_cents"],
    )


def _account_to(account: Account) -> dict[str, Any]:
    raw = asdict(account)
    raw["purchases"] = [
        {
            **purchase,
            "recorded_at": (
                purchase["recorded_at"].isoformat()
                if purchase["recorded_at"] is not None
                else None
            ),
        }
        for purchase in raw["purchases"]
    ]
    return raw


def _bought_this_month(account: Account) -> int:
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def repair(accounts: list[Account]) -> tuple[list[Account], list[str], list[str]]:
    """The accounts with every too-low month total corrected.

    Returns the accounts (in the order given), the shopper ids repaired, and the
    shopper ids left alone because their total was too high rather than too low.
    """
    finding = reconcile_monthly_totals(accounts)
    behind = {
        disagreement.shopper_id
        for disagreement in finding.accounts_that_disagree
        if disagreement.gap_cents > 0
    }
    ahead = [
        disagreement.shopper_id
        for disagreement in finding.accounts_that_disagree
        if disagreement.gap_cents < 0
    ]

    repaired_accounts = [
        replace(account, total_this_month_cents=_bought_this_month(account))
        if account.shopper_id in behind
        else account
        for account in accounts
    ]

    return repaired_accounts, sorted(behind), ahead


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write repaired accounts to stdout; without it, only report",
    )
    args = parser.parse_args(argv)

    accounts = [
        _account_from(json.loads(line)) for line in sys.stdin if line.strip()
    ]
    before = {account.shopper_id: account.total_this_month_cents for account in accounts}
    repaired_accounts, repaired_ids, left_alone = repair(accounts)
    after = {
        account.shopper_id: account.total_this_month_cents
        for account in repaired_accounts
    }

    print(
        f"checked {len(accounts)} accounts; {len(repaired_ids)} behind, "
        f"{len(left_alone)} ahead (left alone)",
        file=sys.stderr,
    )
    for shopper_id in repaired_ids:
        print(
            f"  repair {shopper_id}: total_this_month_cents "
            f"{before[shopper_id]} -> {after[shopper_id]}",
            file=sys.stderr,
        )
    for shopper_id in left_alone:
        print(f"  NOT repaired (total above purchases): {shopper_id}", file=sys.stderr)

    if not args.apply:
        print("dry run: nothing written; pass --apply to emit repaired accounts",
              file=sys.stderr)
        return 0

    for account in repaired_accounts:
        sys.stdout.write(json.dumps(_account_to(account)) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
