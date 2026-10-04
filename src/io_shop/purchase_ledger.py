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
    """The account with this purchase written into it, and its total moved.

    How a purchase is recorded. The lifetime total is added to rather than worked
    out again: the account already carries what has been spent, and a sale changes
    it by exactly the price of the thing sold.

    The month is no longer carried. Every purchase says whether it falls in the
    current month, so what a shopper has spent this month adds up from the history
    whenever anybody wants it - exactly as the lifetime average is derived from the
    purchases rather than read off the account. A second copy of a figure the
    purchases already hold is a copy that has to be kept in step, and the cheapest
    way to keep it in step is not to keep it.
    """
    return replace(
        account,
        purchases=(*account.purchases, purchase),
        total_cents=account.total_cents + purchase.price_cents
    )


def record_purchase_deriving_the_month(account: Account,
                                       purchase: Purchase) -> Account:
    """The same account, without the month's total being moved.

    One total to carry instead of two, which is the saving: a month is not
    something the shop has to keep a figure for. Every purchase says which month
    it falls in, so what a shopper has spent this month adds up from the history
    whenever anybody wants it - exactly as the lifetime average is derived from the
    purchases rather than read off the account (see
    `io_shop.spend_summary.average_spend_per_item`). Keeping a second copy of a
    figure the purchases already hold is the work this path exists to drop.
    """
    return replace(
        account,
        purchases=(*account.purchases, purchase),
        total_cents=account.total_cents + purchase.price_cents
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
