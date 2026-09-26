"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning - and only so many entries.
Io has a few million registered shoppers, so a store that kept one for each of
them who has ever been by would be a store whose size is the shop's traffic
since it last started. That is a heap that climbs all day under perfectly flat
load and only ever comes down at a restart. So this is a cache with a bound
rather than a record: the shoppers kept are the ones seen most recently, and
the least recently seen is dropped to make room. Losing an entry costs one
shopper one greeting, which is what the panel was worth in the first place.
"""

from __future__ import annotations

from collections import OrderedDict


# How many shoppers this process will hold a visit for. Big enough that the
# shoppers actually browsing right now are all in it, small enough that a full
# store is a fixed and unremarkable amount of memory rather than a function of
# how long the process has been up.
_HOW_MANY_SHOPPERS_ARE_KEPT = 10_000

# Most recently seen shopper last, which is what makes the one to drop the one
# at the front.
_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown.

    Dropping the least recently seen shopper if that puts the store over what
    it keeps.
    """
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > _HOW_MANY_SHOPPERS_ARE_KEPT:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and the store still holds it.

    Reading counts as recency: a shopper the shop is still serving should not
    be the one evicted for a shopper who turned up once.
    """
    if shopper_id not in _LAST_SEEN:
        return None

    _LAST_SEEN.move_to_end(shopper_id)

    return _LAST_SEEN[shopper_id]


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for.

    Never more than `_HOW_MANY_SHOPPERS_ARE_KEPT`.
    """
    return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    _LAST_SEEN.clear()
