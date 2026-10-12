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

Whichever path a purchase is recorded through, the account this module hands back
is a true summary of the purchases on it. The paths differ in how the month's
figure is arrived at - added to, or summed from the history - and not in whether
it ends up written down. Nothing downstream derives it: every reader of the
month, from `io_shop.spend_summary` to the monthly statement, reads the stored
field, so a path that left it behind would be serving a figure the purchases
disagree with.

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
    """The same account, with the month's total summed from the purchases.

    One total carried forward instead of two, which is the saving: the month is
    not a figure this path has to keep adding to, because every purchase says
    which month it falls in and the history can be asked. What it does not mean is
    that the field goes unwritten. Nothing downstream re-derives the month - the
    account page, the monthly statement and the shop's own reconciliation all read
    the stored figure - so the sum worked out here is written back, and the record
    this path leaves behind says the same thing its purchases do.

    Summing a month rather than a lifetime is what makes that affordable: the
    arithmetic covers the current month's purchases and not the whole of a
    shopper's past.
    """
    purchases = (*account.purchases, purchase)

    return replace(
        account,
        purchases=purchases,
        total_cents=account.total_cents + purchase.price_cents,
        total_this_month_cents=sum(
            written.price_cents for written in purchases if written.in_current_month
        )
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
