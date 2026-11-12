from __future__ import annotations

from io_shop.accounts import Account, Purchase
from io_shop.repair_monthly_totals import plan_repair, run


def an_account(shopper_id: str, stored: int, *this_month: int,
               earlier: tuple[int, ...] = ()) -> Account:
    purchases = (
        *(Purchase(price_cents=price, in_current_month=False) for price in earlier),
        *(Purchase(price_cents=price, in_current_month=True) for price in this_month),
    )
    return Account(
        shopper_id=shopper_id,
        purchases=purchases,
        total_cents=sum(p.price_cents for p in purchases),
        total_this_month_cents=stored,
    )


def test_a_short_total_is_set_to_what_this_months_purchases_come_to() -> None:
    short = an_account("short", 1000, 1000, 129_731, earlier=(700,))

    plan = plan_repair([short])

    assert [a.total_this_month_cents for a in plan.to_write] == [130_731]
    assert plan.corrections[0].short_by_cents == 129_731


def test_a_total_that_agrees_is_not_touched() -> None:
    fine = an_account("fine", 1500, 1000, 500)

    plan = plan_repair([fine])

    assert plan.to_write == ()
    assert plan.accounts_checked == 1


def test_a_total_above_its_purchases_is_left_for_a_person() -> None:
    over = an_account("over", 9000, 1000)

    plan = plan_repair([over])

    assert plan.to_write == ()
    assert [c.shopper_id for c in plan.left_for_a_person] == ["over"]


def test_a_dry_run_writes_nothing() -> None:
    written: list[Account] = []

    plan = run(lambda: [an_account("short", 0, 500)], written.append,
               report=lambda _line: None)

    assert written == []
    assert not plan.applied
    assert len(plan.to_write) == 1


def test_applying_writes_only_the_short_accounts() -> None:
    written: list[Account] = []
    accounts = [
        an_account("short", 0, 500),
        an_account("fine", 500, 500),
        an_account("over", 900, 500),
    ]

    plan = run(lambda: accounts, written.append, apply=True,
               report=lambda _line: None)

    assert plan.applied
    assert [(a.shopper_id, a.total_this_month_cents) for a in written] == [("short", 500)]
