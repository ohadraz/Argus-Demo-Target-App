from __future__ import annotations

from dataclasses import dataclass

"""One shopper's purchase history, as the account page needs it.

The totals are carried alongside the purchases rather than summed from them,
because that is how a page like this is really assembled: the totals come back
from a query the shop has already run, and the list is what it renders.
"""


@dataclass(frozen=True)
class Purchase:
    price_cents: int
    in_current_month: bool


@dataclass(frozen=True)
class Account:
    purchases: tuple[Purchase, ...]
    total_cents: int
    total_this_month_cents: int
