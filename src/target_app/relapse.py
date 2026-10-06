"""A reverted flag that does not quite stay reverted.

The flag scenario's revert, with something left behind: after the first clean
minute the shop fails a single minute at a time, at gaps that never settle into
a rhythm. Staged by widening the flag's timeline - the shop is broken in those
minutes exactly as it was with the flag on, and the logs report the evaluations
that read it as on, which is what part of a fleet holding a stale flag value
looks like.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from target_app.generator import FlagTimeline

# The clean minutes between one failing minute and the next, in turn. No gap
# repeats within the cycle, so nothing in a window shorter than the whole cycle
# shows a rhythm; and none reaches nine, so every ten minutes hold a failing one.
GAPS: Final = (2, 5, 3, 7, 4, 6, 8)

_A_MINUTE: Final = timedelta(minutes=1)


def failing_minutes_after(revert: datetime, until: datetime) -> list[datetime]:
    """The minutes the shop fails in after a revert at `revert`, up to `until`.

    The first whole minute after the revert is clean - the revert seems to have
    worked - and the one after it fails, then the gaps follow.
    """
    failing = []
    minute = revert.replace(second=0, microsecond=0) + 2 * _A_MINUTE
    turn = 0

    while minute < until:
        failing.append(minute)
        minute += (GAPS[turn % len(GAPS)] + 1) * _A_MINUTE
        turn += 1

    return failing


def with_relapses(timeline: FlagTimeline, up_to: datetime) -> FlagTimeline:
    """`timeline` with every stretch that ended followed by its relapses.

    Each stretch's relapses run until the next stretch began - a flag put back
    on is broken in every minute anyway - or until `up_to` for the last.
    """
    stretches = sorted(
        set(_every_stretch_of(timeline)), key=lambda stretch: stretch.turned_on_at
    )
    widened: list[FlagTimeline] = []

    for index, stretch in enumerate(stretches):
        widened.append(stretch)

        if stretch.turned_off_at is None:
            continue

        until = (
            stretches[index + 1].turned_on_at
            if index + 1 < len(stretches)
            else up_to
        )
        widened.extend(
            FlagTimeline(turned_on_at=minute, turned_off_at=minute + _A_MINUTE)
            for minute in failing_minutes_after(stretch.turned_off_at, until)
        )

    *earlier, latest = widened

    return FlagTimeline(
        turned_on_at=latest.turned_on_at,
        turned_off_at=latest.turned_off_at,
        earlier=tuple(earlier)
    )


def _every_stretch_of(timeline: FlagTimeline) -> list[FlagTimeline]:
    """Each stretch the timeline holds, nested histories included, each stripped
    of its own history so no stretch is counted twice."""
    return [
        *(stretch for earlier in timeline.earlier for stretch in _every_stretch_of(earlier)),
        FlagTimeline(
            turned_on_at=timeline.turned_on_at, turned_off_at=timeline.turned_off_at
        ),
    ]
