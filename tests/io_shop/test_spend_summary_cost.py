"""What the account page's oldest figure costs to render.

That it is correct is covered next door in `test_spend_summary.py`. This is the
other half: the lifetime average is what every request outside the two rollouts
gets, so work it does per purchase per purchase is work the shop does on almost
every page it serves - which is the difference between capacity that settles
and capacity that is saturated the moment it arrives.
"""

from __future__ import annotations

from io_shop.accounts import Account
from io_shop.spend_summary import average_spend_per_item


class _PurchaseThatCountsReads:
    """A purchase that says how often its price was looked at.

    Counting reads rather than timing a loop: what is asserted is the shape of
    the code, and a stopwatch would measure the machine instead.
    """

    reads = 0

    def __init__(self, price_cents: int, in_current_month: bool = False) -> None:
        self._price_cents = price_cents
        self.in_current_month = in_current_month

    @property
    def price_cents(self) -> int:
        type(self).reads += 1
        return self._price_cents


def test_the_lifetime_average_reads_each_price_once() -> None:
    # Re-summing everything bought so far, once per purchase, reads a thousand
    # prices half a million times to arrive at a figure that is the total over
    # the count. One pass reads each of them once.
    how_many = 1000
    prices = [100 + index for index in range(how_many)]
    account = Account(
        shopper_id="shopper-with-a-long-history",
        purchases=tuple(_PurchaseThatCountsReads(price) for price in prices),
        total_cents=sum(prices),
        total_this_month_cents=0,
    )
    _PurchaseThatCountsReads.reads = 0

    figure = average_spend_per_item(account)

    assert figure == sum(prices) // how_many
    assert _PurchaseThatCountsReads.reads <= 5 * how_many
