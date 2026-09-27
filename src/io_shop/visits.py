"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning - and only so many entries.
Io has a few million registered shoppers, so a store that kept one for each of
them who has been by would grow with the shop's audience rather than with
anything the shop can bound, and would go on growing for as long as the process
lives. That is a leak whatever it is called: traffic flat, heap climbing, the
only cure a restart.

So the store holds the most recently seen `MOST_SHOPPERS_REMEMBERED` shoppers
and forgets the rest. Forgetting costs a shopper nothing but the panel - they
see the empty one a first-time visitor sees, and the page renders the same
figure it always would. Keeping them costs the process memory it never gets
back.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Final


# How many shoppers this process will hold a visit for. A ceiling rather than a
# guess at the audience: the panel is worth keeping for whoever is shopping now,
# and what the shop must not do is let a figure it holds for convenience decide
# how much memory a worker uses.
MOST_SHOPPERS_REMEMBERED: Final = 10_000

# Ordered so the store knows which entry to drop: least recently seen first,
# because that is the shopper least likely to be back before the page is served
# again.
_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, forgetting whoever has been away
    longest if the store is full."""
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > MOST_SHOPPERS_REMEMBERED:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and the shop is still holding it.

    Reading counts as being seen, so a shopper who keeps coming back is not the
    one evicted to make room for a shopper who came once.
    """
    shown = _LAST_SEEN.get(shopper_id)

    if shown is not None:
        _LAST_SEEN.move_to_end(shopper_id)

    return shown


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
