from __future__ import annotations

from io_shop.accounts import Account

"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and the cost of
producing it follows the shape of the code rather than anything it is waiting
on - which is why the middle is taken from a sorted history rather than by
removing the cheapest purchase over and over.
"""


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices are ordered once and the middle one is read off. On an even-length
    history that lands on the lower of the two middles, which is the one a
    shopper reading "your typical purchase" can point at - a figure interpolated
    between two prices is one nobody paid. Repeated prices are counted once per
    purchase rather than once per value, because that is what the history is.

    Ordering once rather than taking the cheapest that is left, over and over:
    the two pick the same price, but the second re-scans the whole history for
    every purchase below the middle, which is quadratic work inside a page
    render. The page stays correct as it slows down, so nothing but the latency
    says it is happening.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a history with no purchases in it has no middle")

    return prices[(len(prices) - 1) // 2]
