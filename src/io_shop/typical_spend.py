from __future__ import annotations

from io_shop.accounts import Account

"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has. What it is not
is cheap, and the cost follows the shape of the code rather than anything it is
waiting on.
"""


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by taking the cheapest purchase that is left, over and over, until
    the middle of the history is what remains. On an even-length history that
    lands on the lower of the two middles, which is the one a shopper reading
    "your typical purchase" can point at - a figure interpolated between two
    prices is one nobody paid.
    """
    remaining = [purchase.price_cents for purchase in account.purchases]
    below_the_middle = (len(remaining) - 1) // 2

    for _ in range(below_the_middle):
        remaining.remove(min(remaining))

    return min(remaining)
