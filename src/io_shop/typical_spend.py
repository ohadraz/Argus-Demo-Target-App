from __future__ import annotations

from io_shop.accounts import Account

"""What this shopper's middle purchase cost.

The newest thing on the account page, and the one still behind a rollout. An
average is dragged around by a single expensive buy - one laptop among twenty
coffees averages to a figure describing neither - so the page offers the middle
of the history instead: the price as many purchases sit below as above.

The figure it produces is right for every history the shop has. Its cost is the
thing that has to stay right too: this is worked out on the request path of the
most-visited page in the shop, once per render, over a history that grows for
as long as a shopper keeps shopping. Anything here that is worse than linear in
the length of that history is a latency incident waiting for the rollout to
reach a hundred percent - which is how it arrives, all at once and with the
error rate flat, because every page is still perfectly correct.
"""


def typical_spend_per_item(account: Account) -> int:
    """The middle price in this shopper's history.

    The prices are ordered and the middle one is taken. On an even-length
    history that lands on the lower of the two middles, which is the one a
    shopper reading "your typical purchase" can point at - a figure interpolated
    between two prices is one nobody paid.

    Ordering the whole history costs n log n and is done once. The obvious
    alternative - taking the cheapest that is left, over and over, until the
    middle is what remains - reads more like the sentence above and costs the
    square of the history, because both finding the cheapest and removing it
    walk everything still in hand. That is the shape this function used to have
    and the reason its rollout could not be left on.

    Raises rather than inventing a figure for a shopper who has never bought
    anything: a history with nothing in it has no middle, and a made-up one
    would be a number the page shows and nobody can account for. The boundary
    above turns it into a reported failure - see `io_shop.account_page`.
    """
    prices = sorted(purchase.price_cents for purchase in account.purchases)

    if not prices:
        raise ValueError("a history with no purchases in it has no middle")

    return prices[(len(prices) - 1) // 2]
