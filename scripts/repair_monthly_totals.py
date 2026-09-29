"""One-off: put right the monthly totals `monthly-spend-feature` wrote wrong.

THIS SCRIPT HAS NOT BEEN RUN. A person has to run it, and has to read the dry
run before they do.

Why it exists
-------------
While the flag was on, purchases were recorded through
`io_shop.purchase_ledger.record_purchase_deriving_the_month`, which wrote the
purchase and left the stored `total_this_month_cents` untouched - so every
account that bought anything in that week carries a monthly total short by
everything it bought after the flag went on. The code fix stops the next wrong
write. It does not reach back to the totals already stored, and rewriting stored
money is nobody's to do unattended - see the note at the top of
`io_shop.spend_reconciliation`, which is why the check itself repairs nothing.

What it does
------------
Reads a JSON export of accounts, recomputes each stored monthly total from that
account's own in-month purchases - the same arithmetic the reconciliation check
uses - and reports every account it would change. It writes nothing unless
`--apply` is given, and even then it writes a new file rather than editing the
input.

How to run it
-------------
    python scripts/repair_monthly_totals.py accounts.json
    python scripts/repair_monthly_totals.py accounts.json --apply --out repaired.json

Read the dry-run output first. The gaps should all be positive (totals that
missed writes, never totals that counted twice) and none should be wider than
the month's purchases; anything else is a different fault and is not what this
repairs. Stop and ask if the shape does not match the reconciliation finding.

The export shape it expects, which is what the reconciliation job reads:

    [
      {
        "shopper_id": "...",
        "total_cents": 12345,
        "total_this_month_cents": 999,
        "purchases": [{"price_cents": 500, "in_current_month": true, ...}]
      }
    ]

Unknown fields on an account or a purchase are carried through untouched. The
only field this ever changes is `total_this_month_cents`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def spent_this_month_in(account: dict[str, Any]) -> int:
    """What this account's in-month purchases come to.

    Deliberately the same sum as `io_shop.spend_reconciliation._bought_this_month`
    so that an account this script has repaired is an account that check passes.
    """
    return sum(
        int(purchase["price_cents"])
        for purchase in account.get("purchases", [])
        if purchase.get("in_current_month")
    )


def repairs_for(accounts: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    """Every account whose stored monthly total is not what its purchases come
    to, as (shopper id, what is stored, what it should be)."""
    found = []

    for account in accounts:
        stored = int(account.get("total_this_month_cents", 0))
        correct = spent_this_month_in(account)

        if stored != correct:
            found.append((str(account.get("shopper_id", "?")), stored, correct))

    return found


def repaired(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same accounts with the monthly total put right, and nothing else
    touched."""
    return [
        {**account, "total_this_month_cents": spent_this_month_in(account)}
        for account in accounts
    ]


def _as_money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accounts", type=Path, help="JSON export of accounts")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the repaired export; without this it is a dry run",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to write the repaired export (required with --apply)",
    )
    arguments = parser.parse_args(argv)

    accounts = json.loads(arguments.accounts.read_text())
    to_repair = repairs_for(accounts)

    print(f"{len(accounts)} accounts read, {len(to_repair)} to repair")

    for shopper_id, stored, correct in to_repair:
        print(
            f"  {shopper_id}: stored {_as_money(stored)} "
            f"-> {_as_money(correct)} (short by {_as_money(correct - stored)})"
        )

    if not arguments.apply:
        print("dry run - nothing written. Re-run with --apply --out FILE to write.")
        return 0

    if arguments.out is None:
        print("--apply needs --out: this will not overwrite the export it read.")
        return 2

    arguments.out.write_text(json.dumps(repaired(accounts), indent=2))
    print(f"written to {arguments.out} - load it back and re-run the check.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
