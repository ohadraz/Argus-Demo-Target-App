"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and the cost of
producing it is one sort of that history - not one scan of it per purchase.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices in order, and the one below the middle taken from them. On an
    even-length history that lands on the lower of the two middles, which is the
    one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Ordering once is the same answer as taking the cheapest that is left over
    and over, because the k-th such removal leaves the k-th smallest price as
    the cheapest remaining - and it is the difference between a page whose cost
    follows the history and one whose cost follows the square of it.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a history with no purchases in it has no middle")

    return prices[(len(prices) - 1) // 2]
