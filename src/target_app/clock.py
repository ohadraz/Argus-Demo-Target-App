"""The stack's clock, as the shop reads it.

Simulated time is `SIM_CLOCK_EPOCH + (real - EPOCH) * SIM_CLOCK_SPEED`: the
formula `sim-clock` in the compose file writes its offset by, and every party of
the stack agrees on. Unset, the epoch is 0 and the speed 1, and this is the
wall's clock.

Worked out here rather than preloaded with libfaketime, as the flag provider and
its database are. Preloaded, every clock read went through the library, and on
a starved CPU a burst of requests convoyed the shop's threads behind it until
the whole shop - `/health` included - stalled for ten seconds at a time. Only
the wall clock is the stack's: monotonic time, every timer and every sleep stay
real, as they were under the preload.

Read once, at import, because the stack hands both values to the process as it
starts and never changes them while it runs.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Final

# The real instant the stack's clock starts running at its own rate from, in
# seconds since the Unix epoch.
EPOCH: Final = float(os.environ.get("SIM_CLOCK_EPOCH", "0"))

# How many simulated seconds pass in every real one.
SPEED: Final = float(os.environ.get("SIM_CLOCK_SPEED", "1"))


def now() -> datetime:
    """What time it is on the stack's clock, in UTC."""
    return at(time.time())


def at(real: float, epoch: float = EPOCH, speed: float = SPEED) -> datetime:
    """The stack's time at `real` seconds since the Unix epoch, in UTC."""
    return datetime.fromtimestamp(epoch + (real - epoch) * speed, tz=UTC)
