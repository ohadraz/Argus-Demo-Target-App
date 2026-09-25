from __future__ import annotations

from io_shop.accounts import Account

"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and the cost of
producing it is one sort - so a history twice as long costs about twice as
much, rather than four times.
"""


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices in order, and the one at the middle of them. On an even-length
    history that lands on the lower of the two middles, which is the one a
    shopper reading "your typical purchase" can point at - a figure interpolated
    between two prices is one nobody paid.

    Sorted once rather than by taking the cheapest that is left over and over:
    both find the same price, and repeated prices still count once per purchase
    because the list holds one entry per purchase. The difference is that
    lifting the cheapest out of the list n/2 times rescans the whole history
    each time, which is the page's latency on a long history.

    Raises on a shopper with no purchases at all, as it always has. There is no
    middle of nothing, and the page above turns that into a recorded failure
    rather than a figure - see `io_shop.account_page`.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a shopper with no purchases has no typical purchase")

    return prices[(len(prices) - 1) // 2]
