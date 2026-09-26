"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

It sits on the account page's fallback path, which is to say it runs on every
request the summary cache could not serve. What that costs is therefore the
shop's own latency whenever the cache is gone, so the history is ordered once
and the middle is read off it.
"""

from __future__ import annotations

from io_shop.accounts import Account


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    Found by putting the prices in order and reading the middle one. On an
    even-length history that lands on the lower of the two middles, which is
    the one a shopper reading "your typical purchase" can point at - a figure
    interpolated between two prices is one nobody paid.

    Ordering counts a repeated price once per purchase, not once per value: a
    shopper who bought the same thing five times has a middle, and it is that
    thing.

    Raises on a history with nothing in it, as taking the cheapest of nothing
    always did. A shopper with no purchases has no typical purchase, and a
    figure invented for them would be a number on the page that describes
    nobody - the boundary above turns this into a reported failure, see
    `io_shop.account_page`.
    """
    in_order = sorted(purchase.price_cents for purchase in account.purchases)

    if not in_order:
        raise ValueError("a history with no purchases has no middle")

    return in_order[(len(in_order) - 1) // 2]
