"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and what it
costs follows the shape of the history rather than anything it is waiting on -
so the way it is worked out has to stay proportional to the history. The
shoppers with the longest ones are the page's slowest requests, and there is no
cache underneath this figure for them to fall back on.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order once and taking the middle of them.
    The older reading - take the cheapest that is left, over and over, until
    the middle is what remains - gives the same answer and pays for it with a
    full pass over what remains per purchase, a cost that grows with the square
    of the history and lands entirely on the shoppers who have bought the most.

    On an even-length history this lands on the lower of the two middles, which
    is the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Raises on a history with nothing in it, as taking the cheapest of nothing
    always did. A shopper who has never bought anything has no typical purchase,
    and the account page's boundary is where that becomes a reported failure -
    see `io_shop.account_page`.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a history with no purchases in it has no middle")

    return prices[(len(prices) - 1) // 2]
