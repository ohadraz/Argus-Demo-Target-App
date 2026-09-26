"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

It is worked out while the request is being served, so its cost is the shopper's
wait. One ordering of the history and one index into it: finding the middle by
taking the cheapest that is left, over and over, is a fresh scan per removal and
turns a long history into a slow page for no better an answer.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order and taking the one in the middle. On
    an even-length history that lands on the lower of the two middles, which is
    the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Ordering counts a repeated price once per purchase rather than once per
    value, which is the property the middle depends on: a shopper who bought
    the same thing five times has a middle, and it is that thing.
    """
    in_order = sorted(purchase.price_cents for purchase in account.purchases)
    below_the_middle = (len(in_order) - 1) // 2

    return in_order[below_the_middle]
