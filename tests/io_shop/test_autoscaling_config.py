"""The production autoscaler must not act on readings its own last action produced.

With no scale-down stabilization window the HPA flaps: at high traffic 3
replicas saturate, it scales to 6, per-replica CPU drops under target, and it
immediately scales back to 3. This pins the window at or above the Kubernetes
default.
"""

from __future__ import annotations

import re
from pathlib import Path

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"


def _scale_down_stabilization_seconds() -> int:
    text = VALUES.read_text()
    match = re.search(
        r"^\s*scaleDownStabilizationSeconds:\s*(\d+)\s*$", text, re.MULTILINE
    )
    assert match is not None, "scaleDownStabilizationSeconds must be set explicitly"
    return int(match.group(1))


def test_scale_down_is_stabilized_for_at_least_the_kubernetes_default() -> None:
    assert _scale_down_stabilization_seconds() >= 300
