"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and the cost of
producing it has to stay proportional to that history: this is one of the
figures the page computes when the summary cache cannot be reached, so its cost
is what a cache outage costs the shop.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Taking the cheapest purchase that is left, over and over, until the middle
    is what remains is the same thing as putting the prices in order once and
    reading the middle of them - and ordering them once reads the history a
    handful of times rather than once per purchase.

    On an even-length history this lands on the lower of the two middles, which
    is the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid. A repeated price counts
    once per purchase rather than once per value, because the prices are put in
    order rather than collected.
    """
    in_price_order = sorted(purchase.price_cents for purchase in account.purchases)

    if not in_price_order:
        raise ValueError("no purchases to take the middle of")

    return in_price_order[(len(in_price_order) - 1) // 2]
