"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning. Io has a few million
registered shoppers, so this cannot hold whichever of them have been by - that
is a process whose memory grows with every new shopper until it is killed. It
holds the most recently seen MAX_REMEMBERED_SHOPPERS of them and forgets the
rest, which costs a forgotten shopper nothing more than the empty panel they
would have had on a first visit.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Final

MAX_REMEMBERED_SHOPPERS: Final = 50_000

_LAST_SEEN: OrderedDict[str, str] = OrderedDict()
_LOCK = threading.Lock()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, forgetting the least recently
    seen shoppers once the store is full."""
    with _LOCK:
        _LAST_SEEN[shopper_id] = shown
        _LAST_SEEN.move_to_end(shopper_id)
        while len(_LAST_SEEN) > MAX_REMEMBERED_SHOPPERS:
            _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and have not since been forgotten."""
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
