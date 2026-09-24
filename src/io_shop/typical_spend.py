from __future__ import annotations

from io_shop.accounts import Account

"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and it has to
be cheap as well as right. It sits on the page's fallback path - the one every
request takes when the summary cache cannot be reached - so its cost is the
shop's latency on the day the cache is gone. Anything here that walks the
history once per purchase is a quadratic hidden behind a cache hit ratio, and
it comes out the moment that ratio drops.
"""


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order once and taking the middle of them.
    On an even-length history that lands on the lower of the two middles, which
    is the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Ordering once rather than taking the cheapest that is left over and over:
    both leave the same price - the lower middle - but the second walks what
    remains once per purchase below the middle, which is a whole history's work
    per purchase. That is affordable only while the cache is answering, and
    this function is what runs when it is not.

    Raises `ValueError` for a history with nothing in it, which is a shopper
    who has never bought anything rather than a figure this can make up.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("no purchases to take the middle of")

    return prices[(len(prices) - 1) // 2]
