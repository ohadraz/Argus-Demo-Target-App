"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

Found by sorting the history once, so its cost grows as n log n with the
history rather than with the history's square.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    On an even-length history this is the lower of the two middles, which is
    the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.
    """
    ordered = sorted(purchase.price_cents for purchase in account.purchases)

    return ordered[(len(ordered) - 1) // 2]
