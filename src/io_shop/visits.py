from __future__ import annotations

from collections import OrderedDict

"""Who has looked at their account page, and what they were shown.

The shop keeps this so the page can greet a returning shopper with the figure
they saw last time instead of an empty panel while the real one loads. It is
written on every render and read on the next visit, which is why it lives in
the process rather than in a store: a round trip to the database would cost
more than the panel is worth.

One entry per shopper, the newest render winning - and only so many entries.
That last part is the whole of what this module has to get right. Io has a few
million registered shoppers, a worker process lives for days, and a store that
kept an entry for every shopper who had been by would grow for as long as the
process ran: not with traffic, which is flat, but with *uptime*, because each
hour brings shoppers the process has not seen yet. A heap that climbs while
request volume does not is what that looks like from the outside, and rising
latency across every percentile is what it feels like from inside, as garbage
collection walks more live objects on every pass.

So the store is bounded, and the bound is the point of the code below rather
than a precaution around it. What this panel does is an optimisation - it saves
a shopper a blank rectangle for a moment - and an optimisation is not allowed
to be able to exhaust the process it runs in. Past the bound the
least-recently-seen shopper is dropped, which costs them exactly what a first
visit costs them: the page computes the figure, as it always can.
"""

# How many shoppers a process will hold a visit for. A count rather than a size
# in bytes, because the count is what actually grows: each entry is a shopper id
# and a short string, and it is how many of them there are that decides whether
# this fits.
#
# Ten thousand is chosen against the traffic rather than against the shopper
# table. At 1200 requests a minute the recently-active shoppers number in the
# thousands, so this holds essentially everyone who could plausibly come back
# within the window the panel is useful over - while costing a few megabytes
# that do not move no matter how long the process runs.
MOST_SHOPPERS_REMEMBERED = 10_000

# Ordered by how recently each shopper was seen, least recent first, so that the
# entry to drop is the one at the front. An `OrderedDict` rather than a plain
# dict and a separate queue: the ordering and the lookup have to agree, and two
# structures that have to agree are two structures that eventually do not.
_LAST_SEEN: OrderedDict[str, str] = OrderedDict()


def record_visit(shopper_id: str, shown: str) -> None:
    """Notes what this shopper was last shown, dropping the longest-unseen
    shopper if the store is full.

    The entry moves to the recent end whether it is new or an update, because a
    shopper who is here now is a shopper who might be here again - and a store
    that aged entries by when they were *created* would evict the shop's most
    regular customers first.
    """
    _LAST_SEEN[shopper_id] = shown
    _LAST_SEEN.move_to_end(shopper_id)

    while len(_LAST_SEEN) > MOST_SHOPPERS_REMEMBERED:
        _LAST_SEEN.popitem(last=False)


def what_they_saw_last_time(shopper_id: str) -> str | None:
    """What this shopper was shown on their previous visit, if the process is
    still holding it.

    A shopper who has been dropped answers the same way as one who has never
    been here, and the page treats them the same way: it works the figure out.
    Reading refreshes the entry, so the shoppers this holds are the ones the
    panel is actually being shown to.
    """
    if shopper_id not in _LAST_SEEN:
        return None

    _LAST_SEEN.move_to_end(shopper_id)

    return _LAST_SEEN[shopper_id]


def how_many_shoppers_are_remembered() -> int:
    """How many shoppers this process is holding a visit for.

    Never more than `MOST_SHOPPERS_REMEMBERED`, which is the property worth
    being able to assert from outside.
    """
    return len(_LAST_SEEN)


def forget_every_visit() -> None:
    """Drops the lot.

    What a new process starts with anyway - the shop's own reset, for a
    deployment that wants one without waiting for a restart.
    """
    _LAST_SEEN.clear()
