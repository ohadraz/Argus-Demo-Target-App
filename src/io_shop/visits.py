"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning. Io has a few million
registered shoppers, so this holds only the most recently seen of them - at
most `_MOST_SHOPPERS_REMEMBERED` - and forgets the longest-absent first. A
shopper it has forgotten is treated exactly like one who has never been here,
which is a path the page already has.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

# The most shoppers one process holds a visit for. Without a bound this store
# grows with every distinct shopper who ever visits and is only released by a
# restart - a leak that tracks the size of the customer base, not the traffic.
_MOST_SHOPPERS_REMEMBERED = 100_000

_LAST_SEEN: OrderedDict[str, str] = OrderedDict()
_LOCK = threading.Lock()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, forgetting the longest-absent
    shoppers once more than `_MOST_SHOPPERS_REMEMBERED` are held."""
    with _LOCK:
        _LAST_SEEN[shopper_id] = shown
        _LAST_SEEN.move_to_end(shopper_id)
        while len(_LAST_SEEN) > _MOST_SHOPPERS_REMEMBERED:
            _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before."""
    return _LAST_SEEN.get(shopper_id)


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for."""
    return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    with _LOCK:
        _LAST_SEEN.clear()
