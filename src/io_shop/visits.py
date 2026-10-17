"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning. Io has a few million
registered shoppers, so this holds only the most recently seen of them - at
most `MOST_SHOPPERS_REMEMBERED` - and forgets the least recently seen when it
is full. Forgetting costs a returning shopper nothing but the greeting figure;
holding everyone costs the process its heap.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

MOST_SHOPPERS_REMEMBERED = 50_000

_LAST_SEEN: OrderedDict[str, str] = OrderedDict()
_LOCK = threading.Lock()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, forgetting the least recently
    seen shopper if that takes the store past its bound."""
    with _LOCK:
        _LAST_SEEN[shopper_id] = shown
        _LAST_SEEN.move_to_end(shopper_id)
        while len(_LAST_SEEN) > MOST_SHOPPERS_REMEMBERED:
            _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here recently enough to still be remembered."""
    with _LOCK:
        shown = _LAST_SEEN.get(shopper_id)
        if shown is not None:
            _LAST_SEEN.move_to_end(shopper_id)
        return shown


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for."""
    with _LOCK:
        return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    with _LOCK:
        _LAST_SEEN.clear()
