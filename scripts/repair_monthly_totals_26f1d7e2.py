"""One-off repair for monthly totals that fell behind their purchases.

Written for the incident in which revision 26f1d7e2 stopped moving
`total_this_month_cents` on every recorded purchase. The fix stops new drift;
this recomputes the totals that were already written wrong.

NOT YET RUN. A person has to run it, review its output, and apply the
corrections to the account store - this script never writes to the store.

Usage:
    python scripts/repair_monthly_totals_26f1d7e2.py accounts.json            # dry run
    python scripts/repair_monthly_totals_26f1d7e2.py accounts.json --out corrections.json

`accounts.json` is an export of accounts as a JSON list of objects:
    {"shopper_id": ..., "total_cents": ..., "total_this_month_cents": ...,
     "purchases": [{"price_cents": ..., "in_current_month": ...,
                    "recorded_at": "2026-10-17T20:20:00+00:00" | null}, ...]}

What it corrects: only accounts whose stored month total is LOWER than what
their this-month purchases add up to - the direction this fault produces. The
new value is the sum of `in_current_month` purchase prices. Accounts whose
stored total is HIGHER are a different fault (a double count) and are listed
under `not_corrected`, untouched.

Run it in the same month as the export. It relies on the `in_current_month`
flags in the export; once the month rolls over, the drifted purchases are no
longer flagged as this month and this calculation cannot see them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from io_shop.accounts import Account, Purchase  # noqa: E402
from io_shop.spend_reconciliation import reconcile_monthly_totals  # noqa: E402


def _purchase_from(raw: dict) -> Purchase:
    recorded_at = raw.get("recorded_at")
    return Purchase(
        price_cents=int(raw["price_cents"]),
        in_current_month=bool(raw["in_current_month"]),
        recorded_at=datetime.fromisoformat(recorded_at) if recorded_at else None,
    )


def _account_from(raw: dict) -> Account:
    return Account(
        shopper_id=str(raw["shopper_id"]),
        purchases=tuple(_purchase_from(p) for p in raw.get("purchases", ())),
        total_cents=int(raw["total_cents"]),
        total_this_month_cents=int(raw["total_this_month_cents"]),
    )


def _bought_this_month(account: Account) -> int:
    return sum(p.price_cents for p in account.purchases if p.in_current_month)


def plan_repairs(accounts: list[Account]) -> dict:
    """The corrections to make, and the disagreements deliberately left alone."""
    by_id = {account.shopper_id: account for account in accounts}
    finding = reconcile_monthly_totals(accounts)

    corrections = []
    not_corrected = []

    for disagreement in finding.accounts_that_disagree:
        account = by_id[disagreement.shopper_id]
        entry = {
            "shopper_id": account.shopper_id,
            "stored_total_this_month_cents": account.total_this_month_cents,
            "gap_cents": disagreement.gap_cents,
            "oldest_affected_purchase_at": (
                disagreement.oldest_affected_purchase_at.isoformat()
                if disagreement.oldest_affected_purchase_at else None
            ),
        }
        if disagreement.gap_cents > 0:
            entry["corrected_total_this_month_cents"] = _bought_this_month(account)
            corrections.append(entry)
        else:
            not_corrected.append(entry)

    return {
        "accounts_checked": finding.accounts_checked,
        "corrections": corrections,
        "not_corrected": not_corrected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accounts", type=Path, help="JSON export of accounts")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the corrections here; omitted means dry run")
    args = parser.parse_args(argv)

    raw_accounts = json.loads(args.accounts.read_text())
    plan = plan_repairs([_account_from(raw) for raw in raw_accounts])

    print(
        f"checked {plan['accounts_checked']} accounts: "
        f"{len(plan['corrections'])} to correct, "
        f"{len(plan['not_corrected'])} disagreeing but left alone"
    )

    if args.out is None:
        print("dry run: nothing written. Pass --out to write the corrections file.")
        return 0

    args.out.write_text(json.dumps(plan, indent=2))
    print(f"corrections written to {args.out}; apply them to the account store by hand")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
