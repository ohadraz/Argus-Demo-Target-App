"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and it is
worked out in one ordering of the prices rather than one scan per purchase -
because what this costs is paid once per account-page render, and the page is
rendered thousands of times a minute.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order and taking the one in the middle. On
    an even-length history that lands on the lower of the two middles, which is
    the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Taking the cheapest that is left, over and over, until the middle remains
    gives the same answer and scans the whole history once per purchase
    removed; ordering once gives it for a fraction of the work, and the work is
    what the account page pays for on every request.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a history with no purchases has no middle price")

    return prices[(len(prices) - 1) // 2]
