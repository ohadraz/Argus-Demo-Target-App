"""One-off repair: put stored monthly totals back in line with the purchases.

Written for the incident in which purchases recorded from 2026-10-18T21:30:00Z
onwards were written without moving `total_this_month_cents`. Those totals are
behind the purchases they describe; this rewrites them to what the purchases
this month add up to.

NOT RUN. A person has to run this, deliberately, against the account store:

    python -m io_shop.repair_monthly_totals accounts.jsonl            # report only
    python -m io_shop.repair_monthly_totals accounts.jsonl --write fixed.jsonl

Input is one account per line as JSON, with the fields of `io_shop.accounts`
(`recorded_at` as an ISO-8601 string or null). With `--write`, only the
accounts that were corrected are written out, in the same shape, for loading
back into the store; the input is never modified.

Only totals that have fallen *behind* are repaired - that is the damage this
fault does. A total that is *ahead* of its purchases is reported, not touched.
Lifetime totals are never touched.

Run it within the month the damage was done in, while the `in_current_month`
flags on the stored purchases still describe that month.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, replace
from datetime import datetime

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def _bought_this_month(account: Account) -> int:
    return sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )


def repair_monthly_totals(
    accounts: Iterable[Account],
) -> tuple[list[Account], list[str]]:
    """The corrected accounts, and the shopper ids left alone for being ahead.

    Only accounts whose stored month total is behind their purchases are
    returned corrected; everything else is either correct already or is
    listed as ahead for a person to look at.
    """
    checked = list(accounts)
    by_id = {account.shopper_id: account for account in checked}
    finding = reconcile_monthly_totals(checked)

    corrected: list[Account] = []
    ahead: list[str] = []

    for disagreement in finding.accounts_that_disagree:
        account = by_id[disagreement.shopper_id]

        if disagreement.gap_cents > 0:
            corrected.append(
                replace(account, total_this_month_cents=_bought_this_month(account))
            )
        else:
            ahead.append(disagreement.shopper_id)

    return corrected, ahead


def _account_from(record: dict) -> Account:
    return Account(
        shopper_id=record["shopper_id"],
        purchases=tuple(
            Purchase(
                **{
                    **purchase,
                    "recorded_at": (
                        datetime.fromisoformat(purchase["recorded_at"])
                        if purchase.get("recorded_at")
                        else None
                    ),
                }
            )
            for purchase in record["purchases"]
        ),
        total_cents=record["total_cents"],
        total_this_month_cents=record["total_this_month_cents"],
    )


def _record_from(account: Account) -> dict:
    record = asdict(account)
    record["purchases"] = [
        {
            **purchase,
            "recorded_at": (
                purchase["recorded_at"].isoformat()
                if purchase["recorded_at"] is not None
                else None
            ),
        }
        for purchase in record["purchases"]
    ]
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accounts", help="JSON-lines file of accounts to check")
    parser.add_argument(
        "--write",
        metavar="OUTPUT",
        help="write corrected accounts here as JSON lines (default: report only)",
    )
    args = parser.parse_args(argv)

    with open(args.accounts, encoding="utf-8") as source:
        accounts = [_account_from(json.loads(line)) for line in source if line.strip()]

    corrected, ahead = repair_monthly_totals(accounts)

    print(
        f"checked {len(accounts)}; behind and repairable {len(corrected)}; "
        f"ahead and left alone {len(ahead)}",
        file=sys.stderr,
    )
    for shopper_id in ahead:
        print(f"ahead, not touched: {shopper_id}", file=sys.stderr)

    if args.write:
        with open(args.write, "w", encoding="utf-8") as output:
            for account in corrected:
                output.write(json.dumps(_record_from(account)) + "\n")
        print(f"wrote {len(corrected)} corrected accounts to {args.write}",
              file=sys.stderr)
    else:
        for account in corrected:
            print(
                f"would repair {account.shopper_id}: "
                f"total_this_month_cents -> {account.total_this_month_cents}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
