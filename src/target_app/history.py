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

import psycopg

from target_app.settings import UnleashSettings, get_unleash_settings


# Unleash's audit log, and the column naming the flag an entry is about. An
# entry that is about no flag - a project or a token - has it null, and is left
# alone: this clears the history of two flags, not the provider's whole memory.
_THE_AUDIT_LOG = "events"
_THE_FLAG_IT_IS_ABOUT = "feature_name"


class FlagHistoryUnavailable(Exception):
    """The provider's database could not be reached, so its history stands.

    Its own failure rather than a silent pass, because a reset that quietly
    leaves the last run's toggles in place is exactly the thing this exists to
    prevent - and the next investigation would be the one to discover it, in
    front of an audience.
    """


def forget_the_changes_to(
    flags: Sequence[str], settings: UnleashSettings | None = None
) -> None:
    """Deletes every recorded change to these flags from the provider's log.

    Every change, not the ones since some moment: the reset's own put-backs are
    written a fraction of a second before this runs, and a cutoff would either
    keep them or need a clock this has no reason to hold. A demo's history
    starts when the demo does.
    """
    resolved = settings if settings is not None else get_unleash_settings()

    try:
        with psycopg.connect(resolved.database_url, connect_timeout=5) as connection:
            connection.execute(
                f"DELETE FROM {_THE_AUDIT_LOG} WHERE {_THE_FLAG_IT_IS_ABOUT} = ANY(%s)",
                (list(flags),),
            )
    except psycopg.Error as error:
        raise FlagHistoryUnavailable(
            f"could not clear the flag history in [{resolved.database_url}]: {error}"
        ) from error
