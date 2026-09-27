"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and what it
costs to produce follows the shape of the code. The middle is therefore found
with one ordering pass rather than by rescanning what is left for every
purchase below the middle: the second is correct too, and costs the square of a
history's length in CPU on every render of the shop's most-visited page.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order once and reading the middle of them.
    On an even-length history that lands on the lower of the two middles, which
    is the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    An account with no purchases at all has no middle, and says so rather than
    inventing one; the request boundary turns that into a failed page the way it
    always did.
    """
    in_order = sorted(purchase.price_cents for purchase in account.purchases)

    if not in_order:
        raise ValueError("an account with no purchases has no middle price")

    below_the_middle = (len(in_order) - 1) // 2

    return in_order[below_the_middle]
