"""The production autoscaler must not act on the reading its own scale-up produced."""

from __future__ import annotations

import re
from pathlib import Path

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"


def _scale_down_stabilization_seconds() -> int | None:
    in_autoscaling = False
    for line in VALUES.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_autoscaling = line.strip() == "autoscaling:"
            continue
        if in_autoscaling:
            match = re.match(r"\s+scaleDownStabilizationSeconds:\s*(\d+)", line)
            if match:
                return int(match.group(1))
    return None


def test_scale_down_waits_long_enough_to_avoid_flapping():
    seconds = _scale_down_stabilization_seconds()
    assert seconds is not None, "autoscaling.scaleDownStabilizationSeconds must be set"
    assert seconds >= 300
