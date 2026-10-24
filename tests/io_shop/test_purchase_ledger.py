from __future__ import annotations

from datetime import UTC, datetime

import pytest

from io_shop.accounts import Account, Purchase
from io_shop.purchase_ledger import record_a_purchase, record_purchase
from io_shop.spend_reconciliation import reconcile_monthly_totals

"""Writing a purchase down, and moving the totals that describe it.

Both totals are added to rather than re-derived, which is what makes them worth
carrying and is also the whole of what can go wrong with them: a total is a
second copy of what the purchases already say. The cases below pin what each
addition depends on - the lifetime figure on nothing but the price, the month's on
whether the purchase falls in the month being counted.

What a purchase costs to record is not asserted anywhere. Two additions and a
tuple, and a test timing them would measure the machine it ran on.
"""

SOME_INSTANT = datetime(2026, 9, 22, 14, 10, tzinfo=UTC)


def an_account_with(*prices: int, spent_this_month: int = 0) -> Account:
    """A shopper with a history, and whatever they have spent this month."""
    return Account(
        shopper_id="shopper-with-a-history",
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=False) for price in prices
        ),
        total_cents=sum(prices),
        total_this_month_cents=spent_this_month,
    )


def a_purchase_of(price_cents: int, *, this_month: bool = True) -> Purchase:
    return Purchase(
        price_cents=price_cents,
        in_current_month=this_month,
        recorded_at=SOME_INSTANT,
    )


def test_recording_a_purchase_keeps_it_in_the_history() -> None:
    account = an_account_with(1000, 2000)

    recorded = record_purchase(account, a_purchase_of(500))

    assert [purchase.price_cents for purchase in recorded.purchases] == [1000, 2000, 500]


def test_recording_a_purchase_moves_both_totals() -> None:
    account = an_account_with(1000, 2000)

    recorded = record_purchase(account, a_purchase_of(500))

    assert (recorded.total_cents, recorded.total_this_month_cents) == (3500, 500)


def test_the_month_is_added_to_what_was_already_spent_in_it() -> None:
    # Added to, never worked out again: the figure the account arrived carrying
    # is what the sale moves.
    account = an_account_with(1000, spent_this_month=4000)

    recorded = record_purchase(account, a_purchase_of(500))

    assert recorded.total_this_month_cents == 4500


def test_an_earlier_months_purchase_leaves_this_months_figure_alone() -> None:
    # A purchase counts towards a month only if it falls in it, so a shopper
    # backfilling last month's order does not move what they have spent now.
    account = an_account_with(1000, spent_this_month=4000)

    recorded = record_purchase(account, a_purchase_of(500, this_month=False))

    assert recorded.total_this_month_cents == 4000


def test_an_earlier_months_purchase_still_moves_the_lifetime_total() -> None:
    account = an_account_with(1000, spent_this_month=4000)

    recorded = record_purchase(account, a_purchase_of(500, this_month=False))

    assert recorded.total_cents == 1500


def test_the_instant_a_purchase_was_recorded_at_is_kept() -> None:
    # No page reads it. The shop's own integrity check does, and it is the only
    # thing in a purchase that could ever date a fault in what was written.
    account = an_account_with(1000)

    recorded = record_purchase(account, a_purchase_of(500))

    assert recorded.purchases[-1].recorded_at == SOME_INSTANT


def test_a_shopper_with_no_history_can_be_written_to() -> None:
    a_new_shopper = Account(
        shopper_id="shopper-who-just-registered",
        purchases=(),
        total_cents=0,
        total_this_month_cents=0,
    )

    recorded = record_purchase(a_new_shopper, a_purchase_of(500))

    assert (recorded.total_cents, recorded.total_this_month_cents) == (500, 500)


# Whichever path the rollout selects, the account it writes must still agree with
# itself: the page and the reconciliation both read the stored monthly total.


@pytest.mark.parametrize("derive_the_month", [False, True])
def test_every_write_path_leaves_the_month_agreeing_with_the_purchases(
        derive_the_month: bool) -> None:
    account = an_account_with(1000, 2000)

    for purchase in (a_purchase_of(500), a_purchase_of(700, this_month=False),
                     a_purchase_of(300)):
        account = record_a_purchase(account, purchase, derive_the_month)

    assert account.total_this_month_cents == 800
    assert account.total_cents == 4500
    assert not reconcile_monthly_totals([account]).anything_disagrees


@pytest.mark.parametrize("derive_the_month", [False, True])
def test_an_earlier_months_purchase_moves_no_months_figure_on_either_path(
        derive_the_month: bool) -> None:
    account = record_a_purchase(an_account_with(1000), a_purchase_of(400), derive_the_month)

    recorded = record_a_purchase(
        account, a_purchase_of(500, this_month=False), derive_the_month
    )

    assert recorded.total_this_month_cents == 400


def test_deriving_the_month_corrects_a_total_that_had_already_drifted() -> None:
    drifted = Account(
        shopper_id="shopper-whose-total-fell-behind",
        purchases=(Purchase(price_cents=900, in_current_month=True),),
        total_cents=900,
        total_this_month_cents=0,
    )

    recorded = record_a_purchase(drifted, a_purchase_of(100), derive_the_month=True)

    assert recorded.total_this_month_cents == 1000
    assert not reconcile_monthly_totals([recorded]).anything_disagrees
