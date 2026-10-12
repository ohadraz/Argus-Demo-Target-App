"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has, and what it
costs follows the shape of the code rather than anything it is waiting on -
which is why that shape is one ordering of the prices and not a search repeated
once per purchase.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order once and reading the middle of them.
    On an even-length history that lands on the lower of the two middles, which
    is the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Ordering once rather than taking the cheapest that is left over and over:
    the two agree on every history, repeated prices included, but the repeated
    search costs a page more the longer the history behind it is, and this runs
    inside a request.
    """
    in_order = sorted(purchase.price_cents for purchase in account.purchases)
    below_the_middle = (len(in_order) - 1) // 2

    # `min` of what is left rather than an index into it, so that a history
    # with nothing in it stays the ValueError it has always been rather than
    # becoming a stray index or, worse, a price.
    return min(in_order[below_the_middle:])
