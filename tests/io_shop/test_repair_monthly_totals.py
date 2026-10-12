from __future__ import annotations

from io_shop.accounts import Account, Purchase
from scripts.repair_monthly_totals import corrected, main, needs_repair

"""The one-off repair for the totals the derived write path left behind.

It rewrites stored records, so the cases that matter are the ones about what it
leaves alone: an account that already agrees with its purchases is not touched,
and a dry run writes nothing at all.
"""


def an_account(shopper_id: str, *, stored_monthly: int) -> Account:
    purchases = (
        Purchase(price_cents=1000, in_current_month=False),
        Purchase(price_cents=2000, in_current_month=True),
        Purchase(price_cents=500, in_current_month=True),
    )

    return Account(
        shopper_id=shopper_id,
        purchases=purchases,
        total_cents=3500,
        total_this_month_cents=stored_monthly,
    )


def test_a_total_that_fell_behind_is_put_back_to_what_the_purchases_say() -> None:
    behind = an_account("shopper-behind", stored_monthly=2000)

    assert needs_repair(behind)
    assert corrected(behind).total_this_month_cents == 2500


def test_the_lifetime_total_is_left_exactly_as_it_was() -> None:
    # The broken path still added to it, and a stored history may be trimmed.
    behind = an_account("shopper-behind", stored_monthly=2000)

    assert corrected(behind).total_cents == 3500


def test_an_account_that_already_agrees_is_not_written() -> None:
    saved: list[Account] = []
    agrees = an_account("shopper-fine", stored_monthly=2500)

    repairs = main(lambda: [agrees], saved.append, dry_run=False)

    assert repairs == ()
    assert saved == []


def test_a_dry_run_reports_the_repair_and_writes_nothing() -> None:
    saved: list[Account] = []
    behind = an_account("shopper-behind", stored_monthly=2000)

    repairs = main(lambda: [behind], saved.append, dry_run=True)

    assert [(repair.shopper_id, repair.change_cents) for repair in repairs] == [
        ("shopper-behind", 500)
    ]
    assert saved == []


def test_a_real_run_saves_the_corrected_account() -> None:
    saved: list[Account] = []
    behind = an_account("shopper-behind", stored_monthly=2000)

    main(lambda: [behind], saved.append, dry_run=False)

    assert [account.total_this_month_cents for account in saved] == [2500]
