"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and it costs
one ordering of that history to produce - not one walk of the history per
purchase in it, which is what it used to cost and what put the account page's
p99 into the seconds the first time the rollout reached every request.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices are put in order once and the middle one is read off. On an
    even-length history that lands on the lower of the two middles, which is
    the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Ordered once rather than found by taking the cheapest that is left, over
    and over. That selection was correct and quadratic: every removal scanned
    everything still remaining, so the cost of this figure grew with the square
    of a shopper's history on the most-visited page in the shop. Sorting is the
    same answer - a repeated price occupies one slot per purchase either way -
    at a cost that grows with the history rather than with its square.

    A history with nothing in it has no middle, and says so rather than
    guessing, exactly as the empty `min()` did before it. The request boundary
    turns that into a failed page - see `io_shop.account_page`.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a history with no purchases has no middle price")

    return prices[(len(prices) - 1) // 2]
