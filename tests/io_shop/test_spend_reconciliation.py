from __future__ import annotations

from datetime import UTC, datetime, timedelta

from io_shop.accounts import Account, Purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals

"""The shop's own check on the monthly total it stores beside a history.

Two things are worth pinning, and the second is the one the alert is built on. An
account whose stored total is what its purchases come to is not reported at all,
however large either figure is. And where a total has fallen behind, the gap is
attributed to the most recent purchases - because a total only ever falls further
behind - so the oldest purchase the gap can be made of is where the fault began.

The dating is deliberately refused where the data does not support it. A gap that
is not a sum of this month's purchases dates nothing, and a finding with no date
is better than an alert carrying one nobody can stand behind.
"""

SOME_MONTH_START = datetime(2026, 9, 1, tzinfo=UTC)


def a_purchase_of(price_cents: int, *, days_in: int) -> Purchase:
    return Purchase(
        price_cents=price_cents,
        in_current_month=True,
        recorded_at=SOME_MONTH_START + timedelta(days=days_in),
    )


def an_account_of(shopper_id: str,
                  *purchases: Purchase,
                  stored_monthly_total: int) -> Account:
    """One shopper, with a stored monthly total that may or may not be right."""
    return Account(
        shopper_id=shopper_id,
        purchases=purchases,
        total_cents=sum(purchase.price_cents for purchase in purchases),
        total_this_month_cents=stored_monthly_total,
    )


def an_account_whose_total_is_right() -> Account:
    return an_account_of(
        "shopper-in-good-order",
        a_purchase_of(1000, days_in=1),
        a_purchase_of(2000, days_in=2),
        stored_monthly_total=3000,
    )


def test_a_shop_in_good_order_reports_nothing() -> None:
    found = reconcile_monthly_totals([an_account_whose_total_is_right()])

    assert not found.anything_disagrees


def test_an_account_in_good_order_is_still_counted_as_checked() -> None:
    # A count of disagreements says nothing without the number behind it: forty
    # out of forty thousand and forty out of forty are opposite incidents.
    found = reconcile_monthly_totals([an_account_whose_total_is_right()])

    assert found.accounts_checked == 1


def test_an_account_whose_total_fell_behind_is_reported_with_its_gap() -> None:
    found = reconcile_monthly_totals([
        an_account_of(
            "shopper-whose-total-fell-behind",
            a_purchase_of(1000, days_in=1),
            a_purchase_of(2000, days_in=2),
            stored_monthly_total=1000,
        )
    ])

    assert [
        (account.shopper_id, account.gap_cents)
        for account in found.accounts_that_disagree
    ] == [("shopper-whose-total-fell-behind", 2000)]


def test_the_gap_is_attributed_to_the_most_recent_purchases() -> None:
    # The total is behind by the last two purchases, so the fault reaches back to
    # the earlier of those two and no further: the first purchase is accounted
    # for, and dating the gap from it would backdate the incident by a day.
    found = reconcile_monthly_totals([
        an_account_of(
            "shopper-whose-total-fell-behind",
            a_purchase_of(1000, days_in=1),
            a_purchase_of(2000, days_in=2),
            a_purchase_of(3000, days_in=3),
            stored_monthly_total=1000,
        )
    ])

    assert found.oldest_affected_purchase_at == SOME_MONTH_START + timedelta(days=2)


def test_the_oldest_affected_purchase_is_the_oldest_across_every_account() -> None:
    found = reconcile_monthly_totals([
        an_account_of(
            "shopper-who-drifted-late",
            a_purchase_of(1000, days_in=9),
            stored_monthly_total=0,
        ),
        an_account_of(
            "shopper-who-drifted-first",
            a_purchase_of(1000, days_in=4),
            stored_monthly_total=0,
        ),
    ])

    assert found.oldest_affected_purchase_at == SOME_MONTH_START + timedelta(days=4)


def test_the_largest_gap_is_the_widest_of_them() -> None:
    found = reconcile_monthly_totals([
        an_account_of(
            "shopper-slightly-behind",
            a_purchase_of(500, days_in=4),
            stored_monthly_total=0,
        ),
        an_account_of(
            "shopper-far-behind",
            a_purchase_of(9000, days_in=4),
            stored_monthly_total=0,
        ),
    ])

    assert found.largest_gap_cents == 9000


def test_a_total_that_is_too_high_is_reported_and_not_dated() -> None:
    # The other direction, which is a different fault: a total above what the
    # purchases come to has counted something twice, and no purchase in the
    # history accounts for it.
    found = reconcile_monthly_totals([
        an_account_of(
            "shopper-counted-twice",
            a_purchase_of(1000, days_in=1),
            stored_monthly_total=2000,
        )
    ])

    assert [
        (account.gap_cents, account.oldest_affected_purchase_at)
        for account in found.accounts_that_disagree
    ] == [(-1000, None)]


def test_a_gap_wider_than_the_month_dates_nothing() -> None:
    # The purchases cannot account for it, so there is no purchase to point at.
    # An alert with no date beats one carrying a date the data does not support.
    found = reconcile_monthly_totals([
        an_account_of(
            "shopper-with-an-impossible-gap",
            a_purchase_of(1000, days_in=1),
            stored_monthly_total=-5000,
        )
    ])

    assert found.oldest_affected_purchase_at is None


def test_purchases_from_earlier_months_are_not_part_of_the_sum() -> None:
    # The figure being checked is scoped to the current month, so a history full
    # of older purchases is not a disagreement.
    found = reconcile_monthly_totals([
        Account(
            shopper_id="shopper-idle-this-month",
            purchases=(
                Purchase(price_cents=4000, in_current_month=False),
                Purchase(price_cents=6000, in_current_month=False),
            ),
            total_cents=10_000,
            total_this_month_cents=0,
        )
    ])

    assert not found.anything_disagrees


def test_a_quiet_shop_reports_no_gap_and_no_date() -> None:
    found = reconcile_monthly_totals([])

    assert (
        found.accounts_checked,
        found.largest_gap_cents,
        found.oldest_affected_purchase_at,
    ) == (0, 0, None)
