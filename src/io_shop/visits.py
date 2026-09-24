from __future__ import annotations

from collections import OrderedDict

"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning - and a ceiling on how many
shoppers at once. Io has a few million registered shoppers, so a store that
kept an entry for every one of them who has been by would grow for as long as
the process runs and be bounded only by a restart. That is a leak: retained
memory as a function of process lifetime rather than of load.

So what is held is the most recently seen shoppers and no more. When the store
is full the shopper who has gone longest without being seen is dropped, which
costs that shopper exactly what it costs a shopper who has never been here at
all - the panel starts empty and the page, which works its figure out anyway,
renders as it always did.
"""

# How many shoppers are held at once. Large enough that the shoppers actually
# moving through the shop are all remembered, small enough that a full store is
# a fixed cost the process pays once rather than a number that keeps climbing.
HOW_MANY_SHOPPERS_ARE_KEPT = 10_000

# Ordered by when each shopper was last seen, oldest first - which is what makes
# eviction a question this can answer rather than a guess.
_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown.

    Evicts the shopper who has gone longest without being seen, where noting
    this one would take the store past what it keeps.
    """
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > HOW_MANY_SHOPPERS_ARE_KEPT:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and are still remembered.

    Being asked about counts as being seen: a shopper the shop keeps looking up
    is a shopper worth holding on to, and one nobody has asked about in a
    million visits is the right one to drop.
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
