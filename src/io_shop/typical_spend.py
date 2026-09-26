"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and it is
worked out in one ordering of the prices rather than by walking the history
once per purchase. That distinction is the whole cost of this module: the
figure is computed inside a page render, on the shop's most-visited page, so
work that grows with the square of a shopper's history arrives as the shop's
own latency the moment the rollout reaches everybody.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order once and taking the one in the middle.
    On an even-length history that lands on the lower of the two middles, which
    is the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Ordered once rather than by repeatedly taking the cheapest that is left.
    The two agree on every history - a repeated price still contributes one
    element per purchase, so it is counted once per purchase rather than once
    per value - and they differ only in what they cost: one pass of a sort
    against a scan of the whole history for every purchase below the middle.

    Raises on a shopper with no purchases at all rather than inventing a
    figure, as everything else under `io_shop` does. There is no middle of an
    empty history, and the account page's boundary is where that becomes a rate
    somebody can alert on - see `io_shop.account_page`.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a shopper with no purchases has no typical purchase")

    return prices[(len(prices) - 1) // 2]
