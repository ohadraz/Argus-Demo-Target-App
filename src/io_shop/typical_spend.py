"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has. It is found by
sorting the history once, so its cost grows with the history's length times its
logarithm rather than with the square of it - a shopper with a long history must
not be the one whose page takes seconds.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The history's prices in order, and the one in the middle of them. On an
    even-length history that lands on the lower of the two middles, which is the
    one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Raises on an empty history rather than inventing a middle for it.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("an empty history has no typical purchase")

    return prices[(len(prices) - 1) // 2]
