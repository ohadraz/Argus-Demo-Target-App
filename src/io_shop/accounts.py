from __future__ import annotations

from dataclasses import dataclass

"""One shopper's purchase history, as the account page needs it.

The totals are carried alongside the purchases rather than summed from them,
because that is how a page like this is really assembled: the totals come back
from a query the shop has already run, and the list is what it renders.
"""


@dataclass(frozen=True)
class Purchase:
    """One thing a shopper bought.

    The two fields without defaults are the two every page has always needed:
    what it cost, and whether it falls in the month being looked at. The rest
    arrived with the monthly statement, which is the first thing on the account
    page that describes a purchase rather than counting it - see
    `io_shop.monthly_statement`.

    They carry defaults because the pages that came first do not know about
    them and should not have to: a figure that averages prices is not made
    wrong by a purchase whose category nobody filled in. A default here is
    therefore the honest reading of a sparse record rather than a convenience -
    `"General"` is where a shop's own catalogue puts anything it has not
    classified, and nothing refunded is the ordinary state of a purchase.
    """

    price_cents: int
    in_current_month: bool
    category: str = "General"
    refunded_cents: int = 0
    delivery_cents: int = 0
    discount_cents: int = 0
    instalments_remaining: int = 0


@dataclass(frozen=True)
class Account:
    """One shopper, and what they have bought.

    `shopper_id` is who the history belongs to. It is on the account rather
    than passed beside it because everything the page does with an account it
    does for a shopper - rendering the figure, and remembering that they were
    here.
    """

    shopper_id: str
    purchases: tuple[Purchase, ...]
    total_cents: int
    total_this_month_cents: int
