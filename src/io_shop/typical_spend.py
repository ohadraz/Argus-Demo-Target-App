from __future__ import annotations

from io_shop.accounts import Account

"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and finding it
costs one ordering of the history rather than one scan per purchase. That
matters because this runs while an account page renders: a cost that grows with
the square of a shopper's history is a page whose latency grows with it too,
for every request on this path at once.
"""


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by ordering the prices once and taking the one in the middle. On an
    even-length history that lands on the lower of the two middles, which is the
    one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    The history holds one price per purchase, so a price paid five times counts
    five times towards the middle, which is what a shopper who bought the same
    thing five times means by it.

    Raises `ValueError` for an account that has bought nothing, because there is
    no middle of nothing to show. Catching belongs at the request boundary - see
    `io_shop.account_page`.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("no purchases to take the middle of")

    return prices[(len(prices) - 1) // 2]
