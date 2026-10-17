"""One-off repair for monthly totals left behind by revision 26f1d7e.

NOT RUN AUTOMATICALLY. A person runs this, once, against an export of the
accounts, and loads what it writes back into the store of record themselves.

From 2026-10-10T20:03Z purchases were written without moving the stored
`total_this_month_cents`, so those totals are low by exactly the purchases that
missed it. The purchases themselves were written correctly, so the right figure
is the sum of this month's purchases - the same sum
`io_shop.spend_reconciliation` checks against.

Only totals that are *low* are corrected; that is this fault's signature. A total
that is *high* is some other fault (something counted twice) and is reported,
not touched.

Input: JSON lines on stdin, one account per line:
    {"shopper_id": ..., "total_cents": ..., "total_this_month_cents": ...,
     "purchases": [{"price_cents": ..., "in_current_month": ..., ...}, ...]}

Default is a dry run: a report on stderr, nothing on stdout. With --apply,
the corrected accounts (only those that changed) are written to stdout as JSON
lines in the same shape, for the operator to load back.

Run it before the month rolls over: once `in_current_month` moves on, these
gaps can no longer be recomputed from the records.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def bought_this_month(account: Account) -> int:
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def repair(accounts: Iterable[Account]) -> tuple[list[Account], list[str]]:
    """The accounts whose month total fell behind, corrected; and the ids of
    accounts whose total is too high, which are left alone for a person."""
    checked = list(accounts)
    by_id = {account.shopper_id: account for account in checked}
    finding = reconcile_monthly_totals(checked)

    corrected: list[Account] = []
    left_alone: list[str] = []

    for disagreement in finding.accounts_that_disagree:
        account = by_id[disagreement.shopper_id]

        if disagreement.gap_cents > 0:
            corrected.append(
                replace(account, total_this_month_cents=bought_this_month(account))
            )
        else:
            left_alone.append(disagreement.shopper_id)

    return corrected, left_alone


def _purchase_from(raw: dict) -> Purchase:
    fields = dict(raw)
    recorded_at = fields.get("recorded_at")
    if isinstance(recorded_at, str):
        fields["recorded_at"] = datetime.fromisoformat(
            recorded_at.replace("Z", "+00:00")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write corrected accounts to stdout (default: dry run, report only)",
    )
    args = parser.parse_args(argv)

    accounts = [_account_from(json.loads(line)) for line in sys.stdin if line.strip()]
    originals = {account.shopper_id: account for account in accounts}
    corrected, left_alone = repair(accounts)

    for account in corrected:
        before = originals[account.shopper_id].total_this_month_cents
        print(
            f"{account.shopper_id}: total_this_month_cents {before} -> "
            f"{account.total_this_month_cents}",
            file=sys.stderr,
        )
    for shopper_id in left_alone:
        print(
            f"{shopper_id}: month total is HIGHER than its purchases - not this "
            f"fault, left unchanged for review",
            file=sys.stderr,
        )
    print(
        f"checked {len(accounts)}, corrected {len(corrected)}, "
        f"left for review {len(left_alone)}"
        + ("" if args.apply else " (dry run - nothing written)"),
        file=sys.stderr,
    )

    if args.apply:
        for account in corrected:
            print(json.dumps(_account_to(account)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
