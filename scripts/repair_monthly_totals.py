"""One-off repair: put back the monthly totals that stopped keeping up.

Revision 26f1d7e (deployed 2026-10-17 13:40) recorded purchases without moving
`total_this_month_cents`, so accounts written since then carry a monthly total
lower than their purchases. This finds them with the shop's own check
(`io_shop.spend_reconciliation.reconcile_monthly_totals`) and sets the stored
figure to what the account's current-month purchases add up to.

NOT RUN. A person must run it, against an export of the accounts, and write the
result back to storage themselves - this repository has no storage layer, so the
script reads and writes JSON lines rather than touching a database.

    python scripts/repair_monthly_totals.py < accounts.jsonl            # dry run
    python scripts/repair_monthly_totals.py --apply < accounts.jsonl > fixed.jsonl

One account per input line, shaped like `io_shop.accounts.Account`:
    {"shopper_id": ..., "total_cents": ..., "total_this_month_cents": ...,
     "purchases": [{"price_cents": ..., "in_current_month": ..., ...}, ...]}

Only accounts whose total is *low* (a positive gap: missed writes, this fault)
are corrected. A total that is *high* is a different fault - something counted
twice - and is reported on stderr and left untouched. In-month membership is
taken from each purchase's `in_current_month` as it stands at export time.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, fields, replace
from datetime import datetime

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals

_PURCHASE_FIELDS = {field.name for field in fields(Purchase)}


def _purchase_from(raw: dict) -> Purchase:
    values = {key: value for key, value in raw.items() if key in _PURCHASE_FIELDS}
    if values.get("recorded_at"):
        values["recorded_at"] = datetime.fromisoformat(values["recorded_at"])
    return Purchase(**values)


def _account_from(raw: dict) -> Account:
    return Account(
        shopper_id=raw["shopper_id"],
        purchases=tuple(_purchase_from(each) for each in raw["purchases"]),
        total_cents=raw["total_cents"],
        total_this_month_cents=raw["total_this_month_cents"],
    )


def _as_json(account: Account) -> str:
    def default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"cannot serialise {type(value).__name__}")

    return json.dumps(asdict(account), default=default)


def repaired(accounts: list[Account]) -> tuple[list[Account], list[str]]:
    """The accounts that need correcting, corrected; and the ones left alone."""
    finding = reconcile_monthly_totals(accounts)
    by_shopper = {account.shopper_id: account for account in accounts}

    fixed: list[Account] = []
    left_alone: list[str] = []

    for disagreement in finding.accounts_that_disagree:
        account = by_shopper[disagreement.shopper_id]
        if disagreement.gap_cents <= 0:
            left_alone.append(disagreement.shopper_id)
            continue
        fixed.append(replace(
            account,
            total_this_month_cents=account.total_this_month_cents
            + disagreement.gap_cents,
        ))

    return fixed, left_alone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="print corrected accounts as JSON lines on stdout")
    arguments = parser.parse_args()

    accounts = [_account_from(json.loads(line)) for line in sys.stdin if line.strip()]
    fixed, left_alone = repaired(accounts)

    print(f"checked {len(accounts)} accounts; {len(fixed)} to correct; "
          f"{len(left_alone)} with a total too high, left untouched",
          file=sys.stderr)
    for shopper_id in left_alone:
        print(f"  not corrected (total above purchases): {shopper_id}", file=sys.stderr)

    if arguments.apply:
        for account in fixed:
            print(_as_json(account))
    else:
        for account in fixed:
            print(f"  would set {account.shopper_id} total_this_month_cents="
                  f"{account.total_this_month_cents}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
