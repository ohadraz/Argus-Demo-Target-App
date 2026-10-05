from __future__ import annotations

from datetime import UTC, datetime, timedelta

from io_shop.accounts import Account
from io_shop.spend_summary import (
    average_spend_per_item,
    average_spend_per_item_this_month,
)
from target_app.generator import FlagTimeline
from target_app.integrity import (
    SOMEBODY_BUYS_EVERY,
    THE_CHECK_RUNS_EVERY,
    THE_STANDBY_FELL_BEHIND,
    the_accounts_the_check_examines,
    the_figures_the_promoted_standby_holds,
    what_the_check_found,
)

# One instant for the failover claims below, so the arithmetic in each is about
# the staging rather than about when the suite happened to run.
A_FIXED_INSTANT = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

"""The shop's data-integrity job, and the fault it is here to find.

This is a harness file, so it asserts the *fault is present* - which is what
`tests/io_shop` beside it deliberately does not do. The shop's own suite covers
the write path doing what it says it does; nothing over there covers the cheaper
path dropping the monthly total, exactly as nothing over there covers the
empty-month divisor. Both are planted, and a grader that found either already
failing would have no premise left to attribute a patch's failures to.

Four claims, and the last two are the ones no series could ever show. The drift
is produced by the shop's own write path rather than described here: every account
below is written a purchase at a time through `io_shop.purchase_ledger`, so a test
that says the totals disagree is reporting the bug and not staging it. Putting the
flag back stops the next mis-recorded purchase and corrects nothing already
written. And a restart changes nothing whatsoever, because a restart is nowhere in
any of it.
"""

# A fixed instant to run the check at, so every figure below is arithmetic rather
# than whatever the clock happened to say. The scenario's own onset is a week back
# from whenever it is staged; a week back from here is the same shape.
SOME_INSTANT = datetime(2026, 9, 29, 9, 17, 43, tzinfo=UTC)
WHEN_THE_WRITE_PATH_WENT_LIVE = SOME_INSTANT - THE_CHECK_RUNS_EVERY

# How wide a window the shop's metrics cover, as `app.GENERATED_SPAN_MINUTES`
# says. Named here rather than imported, because what this file needs is the claim
# that the onset is outside it - and a constant that moved with the window would
# make that claim unfalsifiable.
THE_METRICS_REACH_BACK = timedelta(hours=6)


def a_drifting_shop(turned_off_at: datetime | None = None) -> FlagTimeline:
    """The flag's history for a shop whose cheaper write path went live a week
    ago, and was put back at `turned_off_at` if anybody put it back."""
    return FlagTimeline(
        turned_on_at=WHEN_THE_WRITE_PATH_WENT_LIVE, turned_off_at=turned_off_at
    )


def test_a_shop_that_never_shipped_the_cheaper_path_reconciles() -> None:
    # The premise everything else here rests on. Every account is written through
    # the path the shop has always used, so the totals agree with the purchases -
    # and a check that reported a disagreement here would make every finding
    # below unreadable.
    found = what_the_check_found(None, SOME_INSTANT)

    assert not found.anything_disagrees


def test_a_quiet_shop_is_still_examined_rather_than_skipped() -> None:
    # Finding nothing and looking at nothing are different answers, and only one
    # of them is evidence. The count of accounts checked is what separates them.
    found = what_the_check_found(None, SOME_INSTANT)

    assert found.accounts_checked > 0


def test_the_cheaper_write_path_leaves_the_monthly_totals_behind() -> None:
    # The planted fault, reported rather than staged: every purchase below went
    # through the shop's own ledger, and what the check finds is what that ledger
    # actually did.
    found = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    assert len(found.accounts_that_disagree) == found.accounts_checked


def test_every_gap_is_a_total_that_fell_behind_rather_than_ran_ahead() -> None:
    # One-directional, which is what makes the drift accumulate rather than
    # wander: a write that was skipped can only leave a total low.
    found = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    assert all(account.gap_cents > 0 for account in found.accounts_that_disagree)


def test_the_gap_keeps_widening_for_as_long_as_the_path_is_live() -> None:
    # Accumulation is the property, and it is the one that separates this mode
    # from every other scenario in the fixture: the damage is not a condition
    # that holds while something is wrong, it is a quantity that grows.
    a_day_in = what_the_check_found(
        a_drifting_shop(), WHEN_THE_WRITE_PATH_WENT_LIVE + timedelta(days=1)
    )
    a_week_in = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    assert a_week_in.largest_gap_cents > a_day_in.largest_gap_cents


def test_the_oldest_affected_purchase_dates_the_change_that_caused_it() -> None:
    # The whole of what the alert's onset rests on, and the only figure in the
    # incident that dates anything. The check cannot be told when the flag moved -
    # it derives the date from the purchases - and the answer lands within one
    # order of it, because the shop takes an order every two minutes.
    found = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    since_the_flip = found.oldest_affected_purchase_at - WHEN_THE_WRITE_PATH_WENT_LIVE

    assert since_the_flip <= SOMEBODY_BUYS_EVERY


def test_the_oldest_affected_purchase_is_later_than_the_flag_change() -> None:
    # Strictly later, and it matters to a consumer rather than to a reader: a
    # search for what changed at or before the onset has to contain the flip. A
    # purchase recorded in the very instant the flag moved is credited to the old
    # path for exactly this reason.
    found = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    assert found.oldest_affected_purchase_at > WHEN_THE_WRITE_PATH_WENT_LIVE


def test_the_onset_is_older_than_the_metrics_reach() -> None:
    # The claim that makes the mode what it is. An onset inside the window could
    # be measured from the series, and nobody would have to take the shop's word
    # for it - which is the one thing this scenario exists to require.
    found = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    assert found.oldest_affected_purchase_at < SOME_INSTANT - THE_METRICS_REACH_BACK


def test_putting_the_flag_back_stops_the_next_purchase_drifting() -> None:
    # Recovery, in the only terms this mode has. Nothing about the accounts that
    # already drifted changes; what changes is that no purchase after the flip
    # joins them.
    put_back_at = SOME_INSTANT - timedelta(hours=1)

    found = what_the_check_found(a_drifting_shop(put_back_at), SOME_INSTANT)

    assert all(
        account.oldest_affected_purchase_at < put_back_at
        for account in found.accounts_that_disagree
    )


def test_putting_the_flag_back_repairs_nothing() -> None:
    # The property that separates this mode from every other scenario here. A
    # flag going back ends a flag incident; here it ends the cause and leaves the
    # damage, and the same accounts are short by the same amounts.
    still_live = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    put_back = what_the_check_found(
        a_drifting_shop(SOME_INSTANT - timedelta(hours=1)), SOME_INSTANT
    )

    assert (
        len(put_back.accounts_that_disagree),
        put_back.largest_gap_cents,
        put_back.oldest_affected_purchase_at,
    ) == (
        len(still_live.accounts_that_disagree),
        still_live.largest_gap_cents,
        still_live.oldest_affected_purchase_at,
    )


def test_a_write_path_put_back_after_a_rollback_keeps_what_it_wrote_short() -> None:
    # A rollback withdrawn puts the revision back, and the check reads both
    # stretches it ran over: the purchases it skipped the month on the first time
    # are still short, so the oldest of them still dates the change.
    rolled_back_at = SOME_INSTANT - timedelta(hours=2)
    rolled_back = what_the_check_found(a_drifting_shop(rolled_back_at), SOME_INSTANT)

    put_back = what_the_check_found(
        a_drifting_shop(rolled_back_at).again_from(SOME_INSTANT - timedelta(hours=1)),
        SOME_INSTANT
    )

    assert put_back.oldest_affected_purchase_at == rolled_back.oldest_affected_purchase_at
    assert put_back.largest_gap_cents >= rolled_back.largest_gap_cents


def test_the_check_reads_the_same_totals_a_restarted_process_would() -> None:
    # A restart is nowhere in this derivation, which is the whole reason it
    # changes nothing: the fault is in what was written down, and a fresh process
    # reads back the same wrong figures. Run twice at one instant, because that is
    # what a restart amounts to here - the same question asked again.
    before = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    after = what_the_check_found(a_drifting_shop(), SOME_INSTANT)

    assert after == before


def test_the_accounts_are_the_same_accounts_whenever_they_are_asked_for() -> None:
    # Seeded per shopper rather than per call, so the dearest history belongs to
    # the same shopper every time - which is the account the alert's largest gap
    # is about, and a figure that wandered between two reads would be a figure
    # nobody could check.
    once = the_accounts_the_check_examines(a_drifting_shop(), SOME_INSTANT)

    again = the_accounts_the_check_examines(a_drifting_shop(), SOME_INSTANT)

    assert [account.shopper_id for account in once] == [
        account.shopper_id for account in again
    ]


def a_drifted_account() -> Account:
    """One affected shopper, as the shop's write path actually left them."""
    drifting = the_accounts_the_check_examines(a_drifting_shop(), SOME_INSTANT)

    return drifting[0]


def test_the_monthly_figure_on_the_page_is_quietly_low() -> None:
    # The whole reason this mode is worth building. Nothing raises, nothing is
    # slow, and a shopper is shown a number that is wrong - which is the one kind
    # of damage no series can carry.
    account = a_drifted_account()
    bought_this_month = sum(
        purchase.price_cents
        for purchase in account.purchases
        if purchase.in_current_month
    )

    shown = average_spend_per_item_this_month(account)

    assert shown < bought_this_month // len(account.purchases)


def test_the_lifetime_figure_beside_it_is_still_right() -> None:
    # Two figures on one page, disagreeing, and neither of them raising anything.
    # The lifetime one derives its own total from the purchases, so it never read
    # the figure the write path stopped maintaining.
    account = a_drifted_account()

    assert average_spend_per_item(account) == (
        sum(purchase.price_cents for purchase in account.purchases)
        // len(account.purchases)
    )


def test_the_promoted_standby_holds_an_entry_for_every_account_checked() -> None:
    # The stale ones have to be a subset. An account that has bought nothing
    # since replication broke is holding a figure that is still right, and a
    # finding that reported the whole cache wrong would be describing a
    # different incident.
    held = the_figures_the_promoted_standby_holds(A_FIXED_INSTANT)

    assert len(held) == len(the_accounts_the_check_examines(None, A_FIXED_INSTANT))


def test_the_stale_share_is_the_lag_divided_by_how_often_the_shop_sells() -> None:
    # The arithmetic a reader checks the alert against, and the reason the lag
    # is three hours rather than a week: at a week every account has bought and
    # the share saturates, which makes discarding the named entries
    # indistinguishable from discarding the cache.
    held = the_figures_the_promoted_standby_holds(A_FIXED_INSTANT)
    live = {
        account.shopper_id: account
        for account in the_accounts_the_check_examines(None, A_FIXED_INSTANT)
    }
    stale = [
        shopper for shopper, entry in held.items()
        if entry.amount_cents != live[shopper].total_this_month_cents
    ]

    assert len(stale) == THE_STANDBY_FELL_BEHIND // SOMEBODY_BUYS_EVERY


def test_a_stale_entry_is_short_the_purchases_made_since_replication_broke() -> None:
    # Not a corrupted figure and not an invented one: this shop's own correct
    # answer, from earlier. So the gap is exactly what was bought in between.
    broke_at = A_FIXED_INSTANT - THE_STANDBY_FELL_BEHIND
    held = the_figures_the_promoted_standby_holds(A_FIXED_INSTANT)

    for account in the_accounts_the_check_examines(None, A_FIXED_INSTANT):
        since = [
            purchase for purchase in account.purchases
            if purchase.recorded_at > broke_at
        ]
        entry = held[account.shopper_id]

        assert account.total_this_month_cents - entry.amount_cents == sum(
            purchase.price_cents for purchase in since
        )
        assert len(account.purchases) - entry.items_counted == len(since)


def test_an_account_that_has_not_bought_since_still_agrees() -> None:
    broke_at = A_FIXED_INSTANT - THE_STANDBY_FELL_BEHIND
    held = the_figures_the_promoted_standby_holds(A_FIXED_INSTANT)
    quiet = [
        account for account in the_accounts_the_check_examines(None, A_FIXED_INSTANT)
        if all(purchase.recorded_at <= broke_at for purchase in account.purchases)
    ]

    assert quiet, "the staging needs accounts on both sides of the break"

    for account in quiet:
        assert held[account.shopper_id].amount_cents == account.total_this_month_cents
