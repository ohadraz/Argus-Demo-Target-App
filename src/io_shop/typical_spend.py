"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and what it
costs is one ordering of that history rather than one scan per purchase. The
cost follows the shape of the code rather than anything it is waiting on, which
is why the shape is the thing to keep honest: this runs inside a page render on
the most-visited page the shop has.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order once and taking the one in the middle.
    On an even-length history that lands on the lower of the two middles, which
    is the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Raises on a history with nothing in it, as it always has: there is no middle
    of no purchases, and the page above turns that into a failed response rather
    than a figure nobody can account for.
    """
    in_order = sorted(purchase.price_cents for purchase in account.purchases)

    if not in_order:
        raise ValueError("a shopper with no purchases has no typical purchase")

    return in_order[(len(in_order) - 1) // 2]
