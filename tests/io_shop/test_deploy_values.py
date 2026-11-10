from __future__ import annotations

import re
from pathlib import Path

"""Guards on the production deployment values that the service's behaviour
under load depends on, read as text so no YAML library is needed."""

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"


def _autoscaling_block() -> str:
    text = VALUES.read_text()
    match = re.search(r"^autoscaling:\n((?:[ \t]+.*\n?)+)", text, re.MULTILINE)
    assert match, "values-production.yaml has no autoscaling block"
    return match.group(1)


def test_scale_down_waits_long_enough_not_to_chase_its_own_last_action() -> None:
    block = _autoscaling_block()
    found = re.search(r"^\s+scaleDownStabilizationSeconds:\s*(\d+)\s*$", block, re.MULTILINE)

    assert found, "scaleDownStabilizationSeconds must be set explicitly"
    assert int(found.group(1)) >= 60
