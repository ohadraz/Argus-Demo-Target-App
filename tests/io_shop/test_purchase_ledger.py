from __future__ import annotations

from datetime import UTC, datetime

from io_shop.accounts import Account, Purchase
from io_shop.purchase_ledger import (
    record_a_purchase,
    record_purchase,
    record_purchase_deriving_the_month,
)
from io_shop.spend_reconciliation import reconcile_monthly_totals

"""Writing a purchase down, and moving the totals that describe it.

Both totals are added to rather than re-derived, which is what makes them worth
carrying and is also the whole of what can go wrong with them: a total is a
second copy of what the purchases already say. The cases below pin what each
addition depends on - the lifetime figure on nothing but the price, the month's on
whether the purchase falls in the month being counted.

The rollout's path derives the month instead of adding to it, and the cases at
the bottom pin the thing that matters about it: the stored figure every reader in
the shop reads still agrees with the purchases behind it afterwards. They are
asserted through the shop's own reconciliation check rather than against a
number, because that check is what an incident is found by.

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


def test_the_derived_path_leaves_the_month_reconciling_with_the_purchases() -> None:
    # The whole of the incident: a sale written through the rollout's path used
    # to leave the stored monthly figure where it found it, so every reader of
    # it - the account page, the statement, this check - was one purchase behind
    # the history from the moment the flag went on.
    account = an_account_with(1000, spent_this_month=0)

    recorded = record_purchase_deriving_the_month(account, a_purchase_of(500))

    found = reconcile_monthly_totals([recorded])

    assert not found.anything_disagrees


def test_the_derived_path_still_moves_the_lifetime_total() -> None:
    account = an_account_with(1000, spent_this_month=0)

    recorded = record_purchase_deriving_the_month(account, a_purchase_of(500))

    assert recorded.total_cents == 1500


def test_the_derived_path_repairs_a_month_that_had_already_fallen_behind() -> None:
    # The figure comes from the purchases rather than from itself, so an account
    # carrying a week of drift is put right by the next sale recorded for it.
    drifted = Account(
        shopper_id="shopper-who-drifted",
        purchases=(
            a_purchase_of(1000),
            a_purchase_of(2000),
        ),
        total_cents=3000,
        total_this_month_cents=0,
    )

    recorded = record_purchase_deriving_the_month(drifted, a_purchase_of(500))

    assert recorded.total_this_month_cents == 3500


def test_the_derived_path_leaves_an_earlier_months_purchase_out_of_the_month() -> None:
    account = an_account_with(1000, spent_this_month=0)

    recorded = record_purchase_deriving_the_month(
        account, a_purchase_of(500, this_month=False)
    )

    assert (recorded.total_cents, recorded.total_this_month_cents) == (1500, 0)


def test_either_path_leaves_the_same_account_behind() -> None:
    # Flag on and flag off are the same write, which is what makes the rollout
    # withdrawable rather than a migration.
    account = an_account_with(1000, spent_this_month=0)
    purchase = a_purchase_of(500)

    with_the_flag_off = record_a_purchase(account, purchase, derive_the_month=False)
    with_the_flag_on = record_a_purchase(account, purchase, derive_the_month=True)

    assert with_the_flag_off == with_the_flag_on
