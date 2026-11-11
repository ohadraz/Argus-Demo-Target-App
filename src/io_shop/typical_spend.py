"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

`typical-purchase-feature` decides whether a request gets this figure, and a
request that does not get it renders the figure it rendered before.

The figure is found with a single sort of the history, so its cost grows as
n log n in the number of purchases rather than with the square of it.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by ordering the history once and reading the middle of it. On an
    even-length history that lands on the lower of the two middles, which is
    the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Raises ValueError for an empty history, as it always has.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)
    if not prices:
        raise ValueError("an account with no purchases has no typical purchase")

    return prices[(len(prices) - 1) // 2]
