"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning, and only so many entries. The
ceiling is the part worth reading twice: Io has a few million registered
shoppers and this is written on every render, so a store that only ever grew
would hold a number of entries set by how much traffic the process has seen
rather than by anything the deployment configured - and a process whose
footprint tracks its uptime is one that dies of its own success, taking the
reporting nobody notices was coming from it.

What is dropped is whoever was seen longest ago, because the whole value here is
to a shopper who comes *back*. A shopper whose entry has been dropped reads the
same as a shopper who has never been here - `None` - which every caller already
handles, since every shopper is that on their first visit.
"""

from __future__ import annotations

from collections import OrderedDict

# How many shoppers one process will hold a visit for. A number the deployment
# can reason about: entries are small and the panel is worth little, so this is
# set to cover the shoppers plausibly mid-session on one replica rather than to
# cover the register.
MOST_SHOPPERS_REMEMBERED = 10_000

_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, dropping the longest-unseen
    shoppers where that puts the store over its ceiling."""
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > MOST_SHOPPERS_REMEMBERED:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and are still one of the shoppers this process is holding."""
    return _LAST_SEEN.get(shopper_id)


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for."""
    return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    _LAST_SEEN.clear()
