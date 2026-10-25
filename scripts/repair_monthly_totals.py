"""One-off repair: put back the monthly totals monthly-spend-feature left short.

NOT RUN. A person has to run this, deliberately, against the account store.

While monthly-spend-feature was on (from 2026-10-18T07:26Z), purchases were
recorded through a path that did not add them to `total_this_month_cents`, so
affected accounts carry a stored month total *below* what their this-month
purchases come to. This script finds accounts that disagree using
`reconcile_monthly_totals` and, for those whose total is short only, sets the
month total to the sum of their this-month purchase prices.

Accounts whose stored total is *above* their purchases are reported and left
alone: this fault cannot produce that, and rewriting it would be guessing.

The repository has no storage layer, so this works on an export:

    python scripts/repair_monthly_totals.py accounts.json corrections.json
    python scripts/repair_monthly_totals.py accounts.json corrections.json --apply

`accounts.json` is a list of accounts, each
    {"shopper_id": ..., "total_cents": ..., "total_this_month_cents": ...,
     "purchases": [{"price_cents": ..., "in_current_month": ...,
                    "recorded_at": "ISO-8601 or null"}, ...]}

Without `--apply` it only prints what it would change (dry run). With `--apply`
it writes `corrections.json`: one entry per repaired account with the old and new
month total, for whoever applies them to the store. Run it after the fix is
deployed (or with the flag off) so no new short totals are written behind it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from datetime import datetime

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


def corrections_for(accounts: Iterable[Account]) -> tuple[list[dict], list[dict]]:
    """The corrections to make, and the disagreements left for a person."""
    by_id = {account.shopper_id: account for account in accounts}
    finding = reconcile_monthly_totals(by_id.values())
    corrections: list[dict] = []
    left_alone: list[dict] = []

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
            entry["corrected_total_this_month_cents"] = sum(
                purchase.price_cents
                for purchase in account.purchases
                if purchase.in_current_month
            )
            corrections.append(entry)
        else:
            left_alone.append(entry)

    return corrections, left_alone


def _account_from(raw: dict) -> Account:
    return Account(
        shopper_id=raw["shopper_id"],
        purchases=tuple(
            Purchase(
                price_cents=int(p["price_cents"]),
                in_current_month=bool(p["in_current_month"]),
                recorded_at=(
                    datetime.fromisoformat(p["recorded_at"])
                    if p.get("recorded_at") else None
                ),
            )
            for p in raw["purchases"]
        ),
        total_cents=int(raw["total_cents"]),
        total_this_month_cents=int(raw["total_this_month_cents"]),
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    source, destination = argv[0], argv[1]
    apply = "--apply" in argv[2:]

    with open(source, encoding="utf-8") as handle:
        accounts = [_account_from(raw) for raw in json.load(handle)]

    corrections, left_alone = corrections_for(accounts)

    print(f"checked {len(accounts)} accounts")
    print(f"{len(corrections)} short monthly totals to correct")
    for entry in corrections:
        print(
            f"  {entry['shopper_id']}: "
            f"{entry['stored_total_this_month_cents']} -> "
            f"{entry['corrected_total_this_month_cents']} "
            f"(short by {entry['gap_cents']}, since "
            f"{entry['oldest_affected_purchase_at']})"
        )
    print(f"{len(left_alone)} totals above their purchases, left for a person")
    for entry in left_alone:
        print(f"  {entry['shopper_id']}: over by {-entry['gap_cents']}")

    if not apply:
        print("dry run: nothing written. Re-run with --apply to write corrections.")
        return 0

    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(corrections, handle, indent=2)

    print(f"wrote {len(corrections)} corrections to {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
