"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning - and only so many entries.
Io has a few million registered shoppers and anyone at all can ask for a page,
so a store that kept every shopper it ever saw would grow with how many
different people came by rather than with how busy the shop was: flat traffic,
a heap that climbs all day, and a restart as the only cure. It is a cache of a
nicety, not a record of anything, so it is bounded like one. The shoppers whose
renders are most recent are the ones kept; past `MOST_SHOPPERS_REMEMBERED` the
least recently rendered is dropped to make room. A dropped shopper is a shopper
who gets the empty panel for one render, which is what a shopper who has never
been here gets anyway.
"""

from __future__ import annotations

from collections import OrderedDict

# How many shoppers this process will hold a visit for. Enough that the people
# actually coming back are all in here, small enough that a full store is a
# fixed and unremarkable amount of memory rather than a function of how many
# strangers have been by.
MOST_SHOPPERS_REMEMBERED = 10_000

# Ordered by when each shopper was last rendered, oldest first, so that making
# room is taking from the front.
_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, dropping the least recently
    seen shopper where the store is already full."""
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > MOST_SHOPPERS_REMEMBERED:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and the store is still holding it.

    A plain lookup: reading does not count as being seen, because every read
    happens on a render that records a visit of its own a moment later.
    """
    return _LAST_SEEN.get(shopper_id)


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for.

    Never more than `MOST_SHOPPERS_REMEMBERED`.
    """
    return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    _LAST_SEEN.clear()
