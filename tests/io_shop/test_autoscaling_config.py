"""Guards on the autoscaler settings in the production values file.

A zero scale-down stabilization window lets the HPA act on the reading its own
scale-up produced (lower per-replica CPU) and scale straight back down, so the
shop flaps between its floor and ceiling under steady load.
"""

from __future__ import annotations

from pathlib import Path

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"


def _section(name: str) -> dict[str, str]:
    """The `key: value` pairs nested directly under top-level `name:`."""
    found: dict[str, str] = {}
    inside = False
    for raw in VALUES.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" "):
            inside = line.strip() == f"{name}:"
            continue
        if inside and ":" in line:
            key, value = line.strip().split(":", 1)
            found[key.strip()] = value.strip()
    return found


def test_scale_down_is_stabilized():
    autoscaling = _section("autoscaling")
    assert "scaleDownStabilizationSeconds" in autoscaling
    assert int(autoscaling["scaleDownStabilizationSeconds"]) >= 300


def test_replica_bounds_are_ordered():
    autoscaling = _section("autoscaling")
    assert int(autoscaling["minReplicas"]) <= int(autoscaling["maxReplicas"])
