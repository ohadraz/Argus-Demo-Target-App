"""The shop's data-integrity job: the accounts it examines, and when it runs.

The arithmetic is the shop's own and lives with the shop - see
`io_shop.spend_reconciliation`. What is here is the operational half: which
accounts the job covers, how often it runs, and how the accounts got into the
state it finds them in.

Nothing outside the shop can ask for this. There is no endpoint, no tool and no
parameter: the job belongs to whoever runs the shop, on the shop's own schedule,
and the only thing that ever leaves is the alert it fires. An agent that could
re-run the check could confirm its own mitigation, and the whole point of this
incident is that nothing can.

**The accounts are not staged into a broken state; they are written into one.**
Every purchase here goes through `io_shop.purchase_ledger`, through whichever of
its two paths the rollout flag had live at the instant that purchase was
recorded. So the drift the check finds is produced by the shop's own write path
and not described by this module - which is what stops the fixture asserting the
drift it staged, and is why the check can be believed when it says there is none.

No ticker, and that is deliberate rather than a shortcut. Nothing in this service
runs between requests (see `target_app.generator`), because every answer is
derived from the live state at the moment somebody asks - and a background job
firing alerts on its own would race the suite that stages the scenario. So the
interval below is what the job's schedule *is*, said once and reported in the
alert's own words, and the finding is worked out when the monitoring stack asks
whether there is anything to page about. Nothing here depends on the interval:
whatever a real shop chose, the alert carries a finding and a date, and that is
the whole of the channel.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from io_shop.accounts import Account, Purchase
from io_shop.purchase_ledger import record_a_purchase
from io_shop.spend_reconciliation import Reconciliation, reconcile_monthly_totals
from target_app.generator import FlagTimeline

# How often the shop re-adds its shoppers' purchases and compares the sum to the
# totals it stores. Weekly, which is what makes this mode what it is: by the time
# a check like this finds something, hundreds of deployments and flag changes have
# happened since the writing went wrong, so when it *ran* says nothing at all
# about when the fault started. The oldest disagreement is the only thing in the
# finding that dates anything.
#
# Read by the alert, which says what period it covers, and by nothing that
# decides anything. A shop choosing daily or monthly would stage the same
# incident.
THE_CHECK_RUNS_EVERY = timedelta(days=7)

# How many of the shop's accounts have bought something in the current month.
#
# Io has a few million registered shoppers and this is the handful of them the
# check has anything to compare: a total for a month nobody bought anything in is
# zero on both sides. The number is the fixture's, chosen so that the count in
# the alert reads like a real finding rather than a demo's two rows.
_ACCOUNTS_ACTIVE_THIS_MONTH = 240

# How often one of those shoppers buys something, and how far back the purchases
# staged here reach.
#
# The two together decide everything the alert says. The interval sets how much a
# drifting account's gap accumulates - a week of it is twenty-one purchases - and
# the reach-back is longer than the check's own period on purpose, so that an
# affected account has purchases on *both* sides of the flag flip and the gap has
# to be attributed rather than being the whole month.
_A_SHOPPER_BUYS_EVERY = timedelta(hours=8)
_PURCHASES_REACH_BACK = timedelta(days=10)

# And therefore how often the shop takes an order at all, with its shoppers
# staggered evenly across that interval.
#
# Published rather than left implicit, because it is what bounds the alert's onset
# against the change that caused it: the first mis-recorded purchase is within one
# of these of the flag moving, so a consumer looking for what changed at or before
# the onset is looking within two minutes of the flip. That bound is the link
# between the finding and its cause, and a reader should be able to see what sets
# it.
SOMEBODY_BUYS_EVERY = _A_SHOPPER_BUYS_EVERY / _ACCOUNTS_ACTIVE_THIS_MONTH

# Where the shop's buying grid is anchored, so that a purchase's recorded time is a
# fact rather than a figure relative to whenever the check happened to run.
#
# An arbitrary fixed instant on purpose. Anchoring it to anything meaningful - the
# month, the scenario, the day - either slides or has a calendar edge to fall off,
# and nothing anywhere reads this instant: what is read is the times of the
# purchases on the grid, and those have to hold still between two runs of the check
# or the onset is a figure nobody can check.
_THE_GRID_BEGINS = datetime(2026, 1, 1, tzinfo=UTC)

# What one purchase costs, at either end. The shop's own spread, matching the one
# the generator draws an account's history from: a figure the alert quotes as a
# largest gap has to be the size of something somebody could have bought.
_CHEAPEST_PURCHASE_CENTS = 500
_DEAREST_PURCHASE_CENTS = 9000


def what_the_check_found(drifting_write_path: FlagTimeline | None,
                         now: datetime) -> Reconciliation:
    """What one run of the shop's data-integrity job reports, as of now.

    `drifting_write_path` is the stretch over which the rollout had the cheaper
    write path live, and `None` says it never did - which is every scenario but
    one, and a shop with nothing staged. The accounts are then written entirely
    through the path the shop has always used and the check finds nothing, which
    is the answer a quiet shop has to be able to give: a check that reported a
    disagreement on a well shop would make every finding unreadable.
    """
    return reconcile_monthly_totals(
        the_accounts_the_check_examines(drifting_write_path, now)
    )


def the_accounts_the_check_examines(drifting_write_path: FlagTimeline | None,
                                    now: datetime) -> list[Account]:
    """Every account that has bought something this month, as the shop's write
    path has left it.

    Derived from the instant asked for rather than stored, exactly as this
    service's telemetry is: the same instant answers the same both times, and a
    later one answers with whatever has been bought since. That is what makes a
    flag flip mean something here - purchases after it go through the path that
    keeps the total, so the drift stops without a single stored figure being
    corrected.
    """
    return [
        _the_account_of(index, drifting_write_path, now)
        for index in range(_ACCOUNTS_ACTIVE_THIS_MONTH)
    ]


def _the_account_of(index: int,
                    drifting_write_path: FlagTimeline | None,
                    now: datetime) -> Account:
    """One shopper, and their month, recorded a purchase at a time.

    Seeded from the shopper rather than from the clock, so this account reads the
    same however often the check runs - and so the dearest history belongs to the
    same shopper every time, which is the one the alert's largest gap is about.
    """
    entropy = random.Random(f"shopper-{index}")
    account = Account(
        shopper_id=f"shopper-{index}",
        purchases=(),
        total_cents=0,
        total_this_month_cents=0
    )

    for at in _when_this_shopper_bought(index, now):
        account = record_a_purchase(
            account,
            Purchase(
                price_cents=entropy.randrange(
                    _CHEAPEST_PURCHASE_CENTS, _DEAREST_PURCHASE_CENTS
                ),
                in_current_month=True,
                recorded_at=at
            ),
            derive_the_month=_the_cheaper_path_was_live_at(
                at, drifting_write_path
            )
        )

    return account


def _when_this_shopper_bought(index: int, now: datetime) -> list[datetime]:
    """The instants this shopper recorded a purchase at, oldest first.

    Staggered across the buying interval so that the shop takes orders steadily
    rather than in a burst every eight hours. That is what makes the oldest
    affected purchase a good date for the fault: somebody buys within a couple of
    minutes of whatever instant the write path went live, so the first
    mis-recorded purchase is all but on top of the change that caused it.

    On a fixed grid, and this is the part that had to be got right. A purchase is a
    thing that happened at an instant, and the whole of the alert's onset rests on
    that instant holding still. Hung off `now`, the grid slid with the clock: every
    recorded time moved a few milliseconds between one read and the next, so the
    finding was never twice the same and the onset was a figure nobody could check.
    Anchored absolutely, only the *newest* purchase changes as time passes, which
    is what actually happens in a shop.
    """
    stagger = _A_SHOPPER_BUYS_EVERY * (index / _ACCOUNTS_ACTIVE_THIS_MONTH)
    since = now - _PURCHASES_REACH_BACK
    # Which slot of this shopper's the window opens on, counted off the grid rather
    # than walked up to, so a long reach-back costs nothing.
    slots_before = (since - _THE_GRID_BEGINS - stagger) // _A_SHOPPER_BUYS_EVERY
    at = _THE_GRID_BEGINS + stagger + _A_SHOPPER_BUYS_EVERY * slots_before
    bought = []

    while at <= now:
        if at >= since:
            bought.append(at)

        at += _A_SHOPPER_BUYS_EVERY

    return bought


def _the_cheaper_path_was_live_at(at: datetime,
                                  drifting_write_path: FlagTimeline | None) -> bool:
    """Whether the rollout had the cheaper write path live at this instant.

    Read off the flag's own timeline, which is the record everything else in this
    service reconciles against - so a flag somebody put back stops mis-recording
    the very next purchase, and nothing has to be told that they put it back.

    A purchase recorded at the very instant the flag moved goes through the old
    path, and the boundary is deliberate rather than arbitrary. Which path such a
    purchase really took is a race nobody can call, so the conservative reading is
    the one that does not blame a change for a write that may have preceded it -
    and it has a consequence worth having: the oldest affected purchase is then
    always *later* than the flag change, so a consumer looking for what changed at
    or before the onset finds the flip rather than finding it one boundary out of
    reach. The two are two minutes apart here, which is how long it takes somebody
    to buy something.
    """
    if drifting_write_path is None or at <= drifting_write_path.turned_on_at:
        return False

    if drifting_write_path.turned_off_at is None:
        return True

    return at < drifting_write_path.turned_off_at
