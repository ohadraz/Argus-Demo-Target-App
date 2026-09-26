"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning - and only so many entries.
Io has a few million registered shoppers, so a store that kept whichever of
them had been by would be a store whose size is the shopper base rather than
anything the deployment chose: it grows for as long as the process lives, and
the only thing that ever takes any of it back is a restart. That is a leak
whatever it is holding, and it ends with a process that is memory-bound and
slow rather than one that is merely holding stale greetings.

So the store is capped, and the shopper dropped to make room is the one longest
unseen. That is the right one to lose twice over: the greeting is only worth
anything to a shopper who comes back, and a shopper who has not been by in the
last `_HOW_MANY_SHOPPERS_ARE_KEPT` visits is the least likely of everyone here
to be the next one through the door. Losing an entry costs nothing beyond the
panel - a shopper whose entry went is served exactly as a shopper who has never
been here, which the page already renders correctly.
"""

from __future__ import annotations

from collections import OrderedDict


# How many shoppers this process will hold a visit for. A bound rather than a
# tuning knob: what it buys is that the store's size stops being a function of
# how long the process has been up. Big enough that the shoppers who actually
# come back within a shift are still here, small enough that the whole store is
# a few megabytes however long the shop runs.
_HOW_MANY_SHOPPERS_ARE_KEPT = 10_000

# Ordered so that eviction has an answer to "which one": the least recently
# touched shopper is at the front, the most recent at the back.
_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown.

    Where the store is already full of other shoppers, this drops the one
    longest unseen to make room. Nothing about the page depends on the entry
    surviving.
    """
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > _HOW_MANY_SHOPPERS_ARE_KEPT:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and the process is still holding it.

    Reading counts as being seen, so a shopper the shop is still greeting is
    not the one evicted next.
    """
    if shopper_id not in _LAST_SEEN:
        return None

    _LAST_SEEN.move_to_end(shopper_id)

    return _LAST_SEEN[shopper_id]


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for. Never more than
    `how_many_shoppers_can_be_remembered()`."""
    return len(_LAST_SEEN)


def how_many_shoppers_can_be_remembered() -> int:
    """The ceiling on the above - what the process will hold at most, however
    many shoppers have been by."""
    return _HOW_MANY_SHOPPERS_ARE_KEPT


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    _LAST_SEEN.clear()
