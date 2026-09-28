"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and it is
reached by ordering the history once rather than by re-walking what is left of
it per purchase. The cost of this figure follows the shape of the code, and the
shape a rollout can safely be turned on for is the one that grows with the
history rather than with its square.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices are ordered once and the middle one read off. On an even-length
    history that lands on the lower of the two middles, which is the one a
    shopper reading "your typical purchase" can point at - a figure interpolated
    between two prices is one nobody paid. A repeated price counts once per
    purchase rather than once per value, because the ordering holds purchases
    and not distinct prices: a shopper who bought the same thing five times has
    a middle, and it is that thing.

    Raises on a history with nothing in it. A shopper who has never bought
    anything has no middle purchase, and the request boundary above is where
    that becomes a reported failure rather than an invented figure - see
    `io_shop.account_page`.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("no purchases to take the middle of")

    return prices[(len(prices) - 1) // 2]
