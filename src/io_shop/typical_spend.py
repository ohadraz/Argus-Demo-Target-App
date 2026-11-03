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

    Sorts the prices once and takes the middle one. On an even-length history
    that is the lower of the two middles, which is the one a shopper reading
    "your typical purchase" can point at - a figure interpolated between two
    prices is one nobody paid.

    Raises ValueError for an account with no purchases, as it always has.
    """
    prices = [purchase.price_cents for purchase in account.purchases]
    if not prices:
        raise ValueError("typical_spend_per_item() of an empty purchase history")

    return sorted(prices)[(len(prices) - 1) // 2]
