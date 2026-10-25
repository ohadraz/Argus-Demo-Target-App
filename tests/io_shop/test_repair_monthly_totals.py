from __future__ import annotations

from datetime import UTC, datetime

from io_shop.accounts import Account, Purchase
from io_shop.repair_monthly_totals import repair_monthly_totals
from io_shop.spend_reconciliation import reconcile_monthly_totals

AN_INSTANT = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def an_account(shopper_id: str, month_prices: list[int], stored_month: int) -> Account:
    purchases = (
        Purchase(price_cents=900, in_current_month=False),
        *(
            Purchase(price_cents=price, in_current_month=True, recorded_at=AN_INSTANT)
            for price in month_prices
        ),
    )
    return Account(
        shopper_id=shopper_id,
        purchases=purchases,
        total_cents=sum(purchase.price_cents for purchase in purchases),
        total_this_month_cents=stored_month,
    )


def test_a_total_that_fell_behind_is_rewritten_to_what_the_purchases_say() -> None:
    behind = an_account("behind", [500, 700], stored_month=500)

    corrected, ahead = repair_monthly_totals([behind])

    assert [account.total_this_month_cents for account in corrected] == [1200]
    assert ahead == []
    assert not reconcile_monthly_totals(corrected).anything_disagrees


def test_the_lifetime_total_is_not_touched() -> None:
    behind = an_account("behind", [500, 700], stored_month=0)

    corrected, _ = repair_monthly_totals([behind])

    assert corrected[0].total_cents == behind.total_cents


def test_correct_and_ahead_accounts_are_left_alone() -> None:
    correct = an_account("correct", [500], stored_month=500)
    too_high = an_account("too-high", [500], stored_month=900)

    corrected, ahead = repair_monthly_totals([correct, too_high])

    assert corrected == []
    assert ahead == ["too-high"]
