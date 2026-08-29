from __future__ import annotations

"""How widely the new summary is rolled out while its flag is on.

A property of the shop's release, not of the demo: the team put the new figure
behind a flag and pointed it at a slice of traffic. The incident it stages is
therefore a partial one - an elevated error rate against a shop still mostly
working, which is what a canary release looks like when it goes wrong. A flag
that broke every request would be a different and far easier incident to
diagnose.
"""

CANARY_SHARE = 0.4
