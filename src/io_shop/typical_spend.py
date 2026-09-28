"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and what it
costs to produce follows the shape of the code rather than anything it is
waiting on. That is why the middle is found by ordering the prices once rather
than by taking the cheapest that is left over and over: the second is the same
answer at a cost that grows with the square of the history, and this figure is
computed on the shop's most-visited page.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order once and reading the middle of them.
    On an even-length history that lands on the lower of the two middles, which
    is the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Raises on a history with nothing in it, as taking the middle of no
    purchases always has: there is no price to name, and the page above turns
    that into a failed response rather than a made-up figure.
    """
    in_order = sorted(purchase.price_cents for purchase in account.purchases)

    if not in_order:
        raise ValueError("no purchases to take the middle of")

    below_the_middle = (len(in_order) - 1) // 2

    return in_order[below_the_middle]
