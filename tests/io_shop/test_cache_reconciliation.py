"""Asking the purchases whether the cached figures kept up with them.

The companion to `test_spend_reconciliation`, and the cases here are mostly
about telling the two findings apart. Both report figures that disagree with a
shopper's purchases; only one of them is about a copy that can simply be thrown
away. A test that did not pin which copy is wrong would pass for either.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from io_shop.accounts import Account, Purchase
from io_shop.cache_reconciliation import reconcile_cached_summaries
from io_shop.summary_cache import SummaryEntry

SOME_MOMENT = datetime(2026, 3, 11, 9, 0, tzinfo=UTC)


def an_account_that_bought(*prices: int,
                           shopper_id: str = "shopper-1",
                           first_at: datetime = SOME_MOMENT) -> Account:
    """A shopper whose purchases are an hour apart, oldest first."""
    purchases = tuple(
        Purchase(
            price_cents=price,
            in_current_month=True,
            recorded_at=first_at + timedelta(hours=index)
        )
        for index, price in enumerate(prices)
    )

    return Account(
        shopper_id=shopper_id,
        purchases=purchases,
        total_cents=sum(prices),
        total_this_month_cents=sum(prices)
    )


def an_entry_holding(amount_cents: int, items_counted: int) -> SummaryEntry:
    return SummaryEntry(amount_cents=amount_cents, items_counted=items_counted)


def test_a_cached_figure_that_kept_up_is_not_a_disagreement() -> None:
    account = an_account_that_bought(500, 700)

    found = reconcile_cached_summaries(
        [account], {account.shopper_id: an_entry_holding(1200, 2)}
    )

    assert not found.anything_disagrees
    assert found.entries_checked == 1


def test_a_shopper_the_cache_holds_nothing_for_is_not_checked() -> None:
    # A cold cache is a cache working normally: every entry has a first request,
    # and the page recomputes and renders. Counting absences would report an
    # empty cache as an incident.
    found = reconcile_cached_summaries([an_account_that_bought(500)], {})

    assert found.entries_checked == 0
    assert not found.anything_disagrees


def test_a_frozen_figure_is_short_both_the_money_and_the_count() -> None:
    # The two fields together are what separates a stale copy from an arithmetic
    # error: a figure that disagreed about the money and agreed about the count
    # would be miscomputed rather than old.
    account = an_account_that_bought(500, 700, 900)

    found = reconcile_cached_summaries(
        [account], {account.shopper_id: an_entry_holding(500, 1)}
    )
    stale = found.entries_that_disagree[0]

    assert stale.gap_cents == 1600
    assert stale.items_short == 2


def test_a_gap_is_dated_from_the_oldest_purchase_the_entry_never_saw() -> None:
    # What dates the fault. The entry counted one purchase, so the second one is
    # the oldest it does not account for - and that is when it stopped being
    # updated.
    account = an_account_that_bought(500, 700, 900)

    found = reconcile_cached_summaries(
        [account], {account.shopper_id: an_entry_holding(500, 1)}
    )

    assert found.oldest_missing_purchase_at == SOME_MOMENT + timedelta(hours=1)


def test_a_figure_ahead_of_the_ledger_is_reported_with_a_negative_gap() -> None:
    # The opposite fault and a different cause: a copy that is behind has missed
    # purchases, one that is ahead is counting something the ledger does not
    # have. Signed rather than absolute so the two cannot be confused.
    account = an_account_that_bought(500)

    found = reconcile_cached_summaries(
        [account], {account.shopper_id: an_entry_holding(900, 2)}
    )

    assert found.entries_that_disagree[0].gap_cents == -400
    # Nothing dates it: the entry is not short of purchases, so attributing it to
    # one would invent a cause.
    assert found.oldest_missing_purchase_at is None


def test_the_count_checked_is_carried_so_a_share_can_be_read() -> None:
    # Ninety of ninety and ninety of two hundred and forty are the same number
    # and opposite incidents - one says empty the cache, the other says remove
    # these entries and leave the rest.
    accounts = [
        an_account_that_bought(500, 700, shopper_id=f"shopper-{index}")
        for index in range(4)
    ]
    held = {
        account.shopper_id: an_entry_holding(500, 1)
        for account in accounts[:1]
    } | {
        account.shopper_id: an_entry_holding(1200, 2)
        for account in accounts[1:]
    }

    found = reconcile_cached_summaries(accounts, held)

    assert found.entries_checked == 4
    assert len(found.entries_that_disagree) == 1


def test_the_widest_gap_and_the_most_missing_may_be_different_entries() -> None:
    # One shopper bought a single expensive thing; another bought three cheap
    # ones. Reporting them as one entry's figures would describe an entry that
    # does not exist.
    dear = an_account_that_bought(9000, shopper_id="shopper-dear")
    many = an_account_that_bought(100, 100, 100, shopper_id="shopper-many")

    found = reconcile_cached_summaries(
        [dear, many],
        {
            dear.shopper_id: an_entry_holding(0, 0),
            many.shopper_id: an_entry_holding(0, 0)
        }
    )

    assert found.largest_gap_cents == 9000
    assert found.largest_items_short == 3


def test_nothing_disagreeing_reports_no_widest_gap_rather_than_raising() -> None:
    found = reconcile_cached_summaries([], {})

    assert found.largest_gap_cents == 0
    assert found.largest_items_short == 0
    assert found.oldest_missing_purchase_at is None
