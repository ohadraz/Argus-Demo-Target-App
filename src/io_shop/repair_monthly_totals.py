"""One-off repair for the month totals left behind from 2026-10-15T19:42Z.

NOT RUN. A person has to run this deliberately. It rewrites stored data and
that cannot be undone.

From that time, purchases were recorded without moving
`total_this_month_cents`, so those totals are lower than the purchases they
sum. This finds the accounts the shop's own check (`reconcile_monthly_totals`)
flags and sets each stored month total to the sum of this month's purchases.

What it touches, and what it leaves alone:
  - Only accounts whose total is *behind* their purchases (a positive gap). That
    is the damage this fault does. A total that is too high is a different fault
    (something counted twice). Those accounts are reported and not changed.
  - Only `total_this_month_cents`. `total_cents` was moved correctly on both
    write paths.
  - Run it before the month rolls over. `in_current_month` describes the month
    being counted, and after the rollover these totals can no longer be rebuilt
    from the history this way.

By default it only reports. Nothing is written unless `apply=True` / `--apply`.

Command line (accounts as a JSON list, the same shape as `io_shop.accounts`):

    python -m io_shop.repair_monthly_totals accounts.json            # report
    python -m io_shop.repair_monthly_totals accounts.json --apply --out fixed.json

The repository has no storage layer. Whoever runs this either exports accounts
in that shape and loads the output back, or calls `repair_monthly_totals` from
their own store code.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals


@dataclass(frozen=True)
class RepairReport:
    accounts: tuple[Account, ...]
    repaired: tuple[tuple[str, int, int], ...]  # shopper, old total, new total
    left_alone_too_high: tuple[tuple[str, int], ...]  # shopper, gap


def _month_from_purchases(account: Account) -> int:
    return sum(p.price_cents for p in account.purchases if p.in_current_month)


def repair_monthly_totals(accounts: list[Account], apply: bool = False) -> RepairReport:
    """Return the accounts, repaired only if `apply`, plus what was found."""
    finding = reconcile_monthly_totals(accounts)
    behind = {d.shopper_id for d in finding.accounts_that_disagree if d.gap_cents > 0}
    too_high = tuple(
        (d.shopper_id, d.gap_cents)
        for d in finding.accounts_that_disagree
        if d.gap_cents < 0
    )

    repaired: list[tuple[str, int, int]] = []
    out: list[Account] = []

    for account in accounts:
        if account.shopper_id in behind:
            correct = _month_from_purchases(account)
            repaired.append((account.shopper_id, account.total_this_month_cents, correct))
            if apply:
                account = replace(account, total_this_month_cents=correct)
        out.append(account)

    return RepairReport(tuple(out), tuple(repaired), too_high)


def _purchase_from(raw: dict) -> Purchase:
    raw = dict(raw)
    if raw.get("recorded_at"):
        raw["recorded_at"] = datetime.fromisoformat(raw["recorded_at"])
    return Purchase(**raw)


def _account_from(raw: dict) -> Account:
    return Account(
        shopper_id=raw["shopper_id"],
        purchases=tuple(_purchase_from(p) for p in raw["purchases"]),
        total_cents=raw["total_cents"],
        total_this_month_cents=raw["total_this_month_cents"],
    )


def _account_to(account: Account) -> dict:
    raw = asdict(account)
    for purchase in raw["purchases"]:
        if purchase.get("recorded_at") is not None:
            purchase["recorded_at"] = purchase["recorded_at"].isoformat()
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accounts_json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    if args.apply and not args.out:
        parser.error("--apply needs --out")

    with open(args.accounts_json) as handle:
        accounts = [_account_from(raw) for raw in json.load(handle)]

    report = repair_monthly_totals(accounts, apply=args.apply)

    for shopper, old, new in report.repaired:
        verb = "repaired" if args.apply else "would repair"
        print(f"{verb} {shopper}: {old} -> {new}")
    for shopper, gap in report.left_alone_too_high:
        print(f"left alone {shopper}: total too high by {-gap} (a different fault)",
              file=sys.stderr)
    print(f"{len(report.repaired)} of {len(accounts)} accounts behind")

    if args.apply:
        with open(args.out, "w") as handle:
            json.dump([_account_to(a) for a in report.accounts], handle, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
