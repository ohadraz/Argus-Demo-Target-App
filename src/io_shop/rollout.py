from __future__ import annotations

"""How widely the new summary is rolled out while its flag is on.

A canary: the new figure sits behind a flag pointed at a slice of traffic, so a
problem with it shows up against a shop that is still mostly working rather than
against one that is down.
"""

CANARY_SHARE = 0.4
