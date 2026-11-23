"""The flag provider's audit log, as far as a fixture is allowed to touch it.

Unleash's event log is an audit log by design: it can be read through the API
and never written or deleted through it. That is right for a real provider and
wrong for a demo. A reset that puts the flags back still leaves every toggle of
the run just finished in the log - and the put-back adds two more - so the next
investigation reads a window containing changes nobody staged, and reports two
flags "flipping simultaneously" when one of them is last week's demo.

So a reset reaches past the API into the provider's own database, which is the
only way back to a clean world: `docker-compose.yml` publishes that port for
this reason and says so. Nothing else in this service does it, nothing outside
a reset does it, and Argus neither knows it is possible nor could do it - it
holds a flag-provider token and no database at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import psycopg

from target_app.settings import UnleashSettings, get_unleash_settings

# Unleash's audit log, and the column naming the flag an entry is about. An
# entry that is about no flag - a project or a token - has it null, and is left
# alone: this clears the history of two flags, not the provider's whole memory.
_THE_AUDIT_LOG = "events"
_THE_FLAG_IT_IS_ABOUT = "feature_name"
# And when the provider says an entry happened, which is the field a consumer
# windows its search by. `timestamp with time zone`, so an aware instant goes in
# and comes back out as the same moment.
_WHEN_IT_HAPPENED = "created_at"
# And the order the provider wrote entries in: a serial, so every entry written
# after another has the larger one. What picks out the entries a staging wrote,
# rather than the time they say they were written at - the provider stamps that
# on its own clock, which in a sped-up stack trails the one this service works
# out by up to a step of the offset it reads (`sim-clock`), so an entry written
# after a moment this service read can say it was written before it.
_THE_ORDER_IT_WAS_WRITTEN_IN = "id"


class FlagHistoryUnavailable(Exception):
    """The provider's database could not be reached, so its history stands.

    Its own failure rather than a silent pass, because a reset that quietly
    leaves the last run's toggles in place is exactly the thing this exists to
    prevent - and the next investigation would be the one to discover it, in
    front of an audience.
    """


def the_last_recorded_change(settings: UnleashSettings | None = None) -> int:
    """Where the provider's log has got to: its latest entry's place in the
    order it was written in, or 0 for an empty log. What a staging reads before
    it writes, so it can tell its own entries from every one before them."""
    resolved = settings if settings is not None else get_unleash_settings()

    try:
        with psycopg.connect(resolved.database_url, connect_timeout=5) as connection:
            row = connection.execute(
                f"SELECT COALESCE(MAX({_THE_ORDER_IT_WAS_WRITTEN_IN}), 0) FROM {_THE_AUDIT_LOG}"
            ).fetchone()
    except psycopg.Error as error:
        raise FlagHistoryUnavailable(
            f"could not read the flag history in [{resolved.database_url}]: {error}"
        ) from error

    return int(row[0]) if row is not None else 0


def forget_the_changes_to(
    flags: Sequence[str],
    after: int | None = None,
    settings: UnleashSettings | None = None,
) -> None:
    """Deletes the recorded changes to these flags from the provider's log:
    every one, or those written after the entry `after` names
    (`the_last_recorded_change`).

    Every change for a reset, not the ones since some moment: the reset's own
    put-backs are written a fraction of a second before this runs, and a cutoff
    would either keep them or need a clock this has no reason to hold. A demo's
    history starts when the demo does.

    After `after` for a staging, which puts the flags back before it moves them
    and has to clear only that: what was recorded before it happened when it
    happened, and an earlier incident a later one is compared with is part of
    it. By the order the entries were written in, never by when they say they
    were: see `_THE_ORDER_IT_WAS_WRITTEN_IN`.
    """
    resolved = settings if settings is not None else get_unleash_settings()
    since_clause = (f" AND {_THE_ORDER_IT_WAS_WRITTEN_IN} > %s"
                    if after is not None else "")
    parameters = (list(flags), after) if after is not None else (list(flags),)

    try:
        with psycopg.connect(resolved.database_url, connect_timeout=5) as connection:
            connection.execute(
                f"DELETE FROM {_THE_AUDIT_LOG} WHERE {_THE_FLAG_IT_IS_ABOUT} = ANY(%s)"
                + since_clause,
                parameters,
            )
    except psycopg.Error as error:
        raise FlagHistoryUnavailable(
            f"could not clear the flag history in [{resolved.database_url}]: {error}"
        ) from error


def record_the_change_as_having_happened_at(
    flags: Sequence[str],
    at: datetime,
    after: int,
    settings: UnleashSettings | None = None,
) -> None:
    """Moves every recorded change to these flags written after the entry
    `after` names (`the_last_recorded_change`) back to `at`.

    The same reach past the API that the eraser above makes, for the same reason
    and with the same justification: an audit log that cannot be written is right
    for a real provider and wrong for a demo, because a demo needs a past.

    Every scenario here gets its past by backdating its *telemetry* - the
    incident is recorded as having begun minutes, or for silent data corruption
    a week, before it was staged - and a flag change left where the provider
    wrote it would be that much newer than the incident it caused. That is not
    a rounding error: a consumer looks for changes up to the onset, so a change
    dated after it is outside every window anybody would look in, and one found
    anyway is a change that cannot have caused what began before it. So the
    change is recorded when the change happened.

    `after` is what keeps this to the staging toggles. A demo's log may hold
    changes from before this scenario was staged, and those happened when they
    happened; only what seeding just wrote is moved. Picked out by the order
    they were written in, never by when they say they were: an entry the
    provider stamped a moment behind this service's clock was once left where
    it was written, after the onset, and the incident's own change went unread.

    Its own failure rather than a silent pass, exactly as the eraser's is. A
    scenario staged with its flag change at the wrong instant looks perfectly well
    from the outside and is unanswerable, which is the worst of the two ways to
    fail.
    """
    resolved = settings if settings is not None else get_unleash_settings()

    try:
        with psycopg.connect(resolved.database_url, connect_timeout=5) as connection:
            connection.execute(
                f"UPDATE {_THE_AUDIT_LOG} SET {_WHEN_IT_HAPPENED} = %s "
                f"WHERE {_THE_FLAG_IT_IS_ABOUT} = ANY(%s) "
                f"AND {_THE_ORDER_IT_WAS_WRITTEN_IN} > %s",
                (at, list(flags), after),
            )
    except psycopg.Error as error:
        raise FlagHistoryUnavailable(
            f"could not backdate the flag history in [{resolved.database_url}]: {error}"
        ) from error
