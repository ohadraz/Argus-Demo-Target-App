"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning. Io has a few million
registered shoppers, so this cannot hold every one who has been by: it keeps
the most recently seen ones, up to a fixed bound, and forgets the least
recently seen beyond that. A forgotten shopper simply gets the empty panel,
which is what they would get on a fresh process anyway.
"""

from __future__ import annotations

from collections import OrderedDict

MAX_SHOPPERS_REMEMBERED = 50_000

_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, forgetting the least recently
    seen shopper if that takes the store past its bound."""
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)
    while len(_LAST_SEEN) > MAX_SHOPPERS_REMEMBERED:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if they have been
    here before and are still remembered."""
    shown = _LAST_SEEN.get(shopper_id)
    if shown is not None:
        _LAST_SEEN.move_to_end(shopper_id)
    return shown


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for."""
    return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    _LAST_SEEN.clear()
