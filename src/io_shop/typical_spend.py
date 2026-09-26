"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and it is
reached by ordering the prices once rather than by picking the cheapest one
out over and over. The second of those is what a median looks like when it is
written as a loop, and its cost grows with the square of the history - which
an account page pays in full, on every request, for the shoppers who have
bought the most.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order once and reading off the one in the
    middle. On an even-length history that lands on the lower of the two
    middles, which is the one a shopper reading "your typical purchase" can
    point at - a figure interpolated between two prices is one nobody paid.

    Raises on a history with no purchases at all, as it always has: there is no
    middle of nothing, and the page's boundary is where that becomes a reported
    failure rather than a made-up figure - see `io_shop.account_page`.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a history with no purchases has no middle")

    return prices[(len(prices) - 1) // 2]
