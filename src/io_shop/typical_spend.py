"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

`typical-purchase-feature` decides whether a request gets this figure, and a
request that does not get it renders the figure it rendered before.

The figure is found with a single sort, so its cost grows as n log n in the
length of the history rather than with its square.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices in order, taken at the middle. On an even-length history that
    lands on the lower of the two middles, which is the one a shopper reading
    "your typical purchase" can point at - a figure interpolated between two
    prices is one nobody paid.

    Raises `ValueError` for a history with no purchases, which has no middle.
    """
    ordered = sorted(purchase.price_cents for purchase in account.purchases)

    if not ordered:
        raise ValueError("a history with no purchases has no middle price")

    return ordered[(len(ordered) - 1) // 2]
