"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and what it
costs follows the shape of the code. It is rendered on the shop's most-visited
page, so the shape has to be one whose cost grows with the history rather than
with the square of it: a render that pegs a core for a shopper with a long
history takes the whole worker's spare capacity with it, and the first thing
that goes is not the page - it is everything else the process was supposed to
find time for.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices are put in order once and the middle one is read off. On an
    even-length history that lands on the lower of the two middles, which is
    the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Ordering once rather than taking the cheapest that is left over and over:
    the two pick the same price, and only the first of them costs an amount
    that a long history can survive being asked for on every page render.

    Raises `ValueError` for a history with nothing in it, which is the account
    that has never bought anything - the same failure the shop's other figures
    raise for it, recorded at the request boundary.
    """
    in_order = sorted(purchase.price_cents for purchase in account.purchases)

    if not in_order:
        raise ValueError("no purchases to take the middle of")

    return in_order[(len(in_order) - 1) // 2]
