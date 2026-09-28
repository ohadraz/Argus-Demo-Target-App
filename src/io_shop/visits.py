"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning - and only ever so many of
them. Io has a few million registered shoppers, so a store that kept an entry
for each one that had been by would grow with the process's uptime rather than
with the traffic it is serving: the same flat request rate, an ever larger
heap, and a restart as the only thing that helped. So it is bounded. Once it is
full, the shopper who has gone longest without being seen makes room for the
one who just arrived.

That is safe to do because the panel is an optimisation and not a record.
Forgetting a shopper costs them one empty panel on their next visit, which is
what every first visit gets anyway; the figure itself is worked out from the
account, not from here.
"""

from __future__ import annotations

from collections import OrderedDict

# How many shoppers the process will hold a visit for at once. A ceiling rather
# than no number at all, because the number is what turns unbounded growth into
# a steady state: the store's footprint is this many entries however long the
# shop has been up. Large enough that the shoppers who come back within a
# stretch of traffic are still remembered, small enough to be a fixed cost.
HOW_MANY_SHOPPERS_ARE_KEPT = 10_000

# Most recently seen shopper last, which is what makes the one to drop the one
# at the front.
_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, forgetting the shopper seen
    longest ago if the store is already full."""
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > HOW_MANY_SHOPPERS_ARE_KEPT:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here recently enough to still be held.

    Reading counts as being seen: a shopper whose panel is still being used is
    not the shopper to forget when room is needed.
    """
    if shopper_id not in _LAST_SEEN:
        return None

    _LAST_SEEN.move_to_end(shopper_id)

    return _LAST_SEEN[shopper_id]


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for - never more than
    `HOW_MANY_SHOPPERS_ARE_KEPT`."""
    return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    _LAST_SEEN.clear()
