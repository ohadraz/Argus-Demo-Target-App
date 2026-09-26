"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning - and only so many entries.
Io has a few million registered shoppers, so a store that kept one for every
shopper who has ever been by would grow with the shop's whole audience and
never shrink, which is a leak rather than a cache: nothing in a long-running
process would ever take any of it back. So the store is capped, and the
shopper given up when it is full is the one least recently seen.

Giving one up costs nothing a page cannot bear. A shopper whose entry went is
indistinguishable from a shopper who has never been here, and the page already
handles that on every first visit - it renders the panel from the figure it
works out. The store is an optimisation for the shoppers who keep coming back,
and those are precisely the ones the cap keeps.
"""

from __future__ import annotations

from collections import OrderedDict


# How many shoppers this process will hold a visit for at once. A bound rather
# than a policy knob: what it buys is that the store's cost is a property of the
# code and not of how many different people happened to visit today. Large
# enough that the shoppers who come back within a day are still here, small
# enough that a full store is a footnote in the heap rather than the heap.
MOST_SHOPPERS_REMEMBERED = 10_000

_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown.

    Where the store is full, the shopper who has gone longest without being
    seen is given up to make room. That is what keeps this a fixed cost: the
    entries go out at the rate new ones come in, instead of accumulating for as
    long as the process lives.
    """
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > MOST_SHOPPERS_REMEMBERED:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and the shop is still holding it.

    Reading counts as being seen, so a shopper the shop hears from stays ahead
    of the shoppers it does not.
    """
    if shopper_id not in _LAST_SEEN:
        return None

    _LAST_SEEN.move_to_end(shopper_id)

    return _LAST_SEEN[shopper_id]


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for."""
    return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    _LAST_SEEN.clear()
