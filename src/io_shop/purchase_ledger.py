"""Writing a purchase down - the other side of what the account page reads.

An account carries two running totals beside its purchases: what the shopper has
spent altogether, and what they have spent this month. Recording a sale means
writing the purchase and moving both of them, and this is where that happens.

Running totals rather than figures derived on demand, because that is what they
are for. The account page is the most-visited page the shop has and a history is
long; a total that was summed on every render would make every page pay for the
whole of a shopper's past. What that buys costs something in return, and the cost
is the only thing worth knowing about this module: a total is a second copy of
what the purchases already say, so a write that moves one and not the other leaves
the shop holding two answers to the same question. Nothing here notices if that
happens - see `io_shop.spend_reconciliation`, which is the shop's own check on
exactly this.

Which path a purchase is recorded through arrives here already decided, exactly
as the choice of figure does on the read side - see `io_shop.spend_summary`.
Nothing in this module reads a flag, and nothing in it knows a rollout is
happening.
"""

from __future__ import annotations

from dataclasses import replace

from io_shop.accounts import Account, Purchase


def record_purchase(account: Account, purchase: Purchase) -> Account:
    """The account with this purchase written into it, and both totals moved.

    How a purchase has always been recorded. Each total is added to rather than
    worked out again: the account already carries what has been spent, and a sale
    changes it by exactly the price of the thing sold.

    The month is the part worth reading twice. A purchase counts towards the
    month's total only if it falls in the month being counted, so that addition is
    conditional where the lifetime one is not - and a shopper backfilling an older
    order leaves this month's figure exactly where it was.
    """
    return replace(
        account,
        purchases=(*account.purchases, purchase),
        total_cents=account.total_cents + purchase.price_cents,
        total_this_month_cents=(
            account.total_this_month_cents + purchase.price_cents
            if purchase.in_current_month
            else account.total_this_month_cents
        )
    )


def record_purchase_deriving_the_month(account: Account,
                                       purchase: Purchase) -> Account:
    """The same account, with the month's total derived rather than added to.

    The saving this path exists for is that the month is never accumulated: a
    figure that is re-derived from the purchases cannot fall behind them by a
    write that was missed, which is the one thing that can go wrong with a
    running total.

    What it does *not* do is stop storing the figure. Every reader of an account
    in this shop reads `total_this_month_cents` - the monthly average on the
    account page, the statement's headline, and the shop's own reconciliation
    check - so a write path that left the stored figure where it found it would
    leave every one of them reading a month that stopped at the moment this path
    was switched on. The derived figure is therefore written down here, which is
    the only place that knows the history has just changed.

    An account whose stored month had already drifted is put right by the next
    purchase recorded through this path, because the figure comes from the
    purchases rather than from itself.
    """
    purchases = (*account.purchases, purchase)

    return replace(
        account,
        purchases=purchases,
        total_cents=account.total_cents + purchase.price_cents,
        total_this_month_cents=spent_this_month_in(purchases)
    )


def spent_this_month_in(purchases: tuple[Purchase, ...]) -> int:
    """What this month's purchases come to, added up from the history.

    The same arithmetic `io_shop.spend_reconciliation` checks a stored total
    against, and it is here so that the writer and the check cannot disagree
    about what the month is.
    """
    return sum(
        purchase.price_cents
        for purchase in purchases
        if purchase.in_current_month
    )


def record_a_purchase(account: Account,
                      purchase: Purchase,
                      derive_the_month: bool) -> Account:
    """The account this purchase leaves behind, written through whichever path
    the rollout selects.

    The decision arrives made, like every other rollout decision in this shop:
    whoever handled the sale was told by the flag SDK which cohort this shopper
    is in, and hands the answer down. A write path that evaluated a flag for
    itself would be a second place the rollout is decided, and the two would
    eventually disagree about which shoppers are in it.
    """
    if derive_the_month:
        return record_purchase_deriving_the_month(account, purchase)

    return record_purchase(account, purchase)
