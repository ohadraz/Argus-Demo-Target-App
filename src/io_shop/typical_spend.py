"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has. It is found by
sorting the history once, so its cost grows only a little faster than the
history does - a long-standing shopper's page must not cost the square of their
past.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices are sorted once and the middle one is read off. On an
    even-length history that lands on the lower of the two middles, which is
    the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Raises `ValueError` on an empty history, as it always has: there is no
    middle of nothing, and the page boundary is where that is recorded.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("there is no typical purchase in an empty history")

    return prices[(len(prices) - 1) // 2]
