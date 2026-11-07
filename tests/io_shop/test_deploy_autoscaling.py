"""The production autoscaler must not scale down on the reading its own scale-up produced.

With no scale-down stabilization window, the HPA drops back to the replica
floor the moment the extra replicas bring per-replica CPU under target, then
scales up again once the floor saturates - capacity alternating every few
minutes under steady load.
"""

from pathlib import Path

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"


def _autoscaling_block():
    block = {}
    inside = False
    for raw in VALUES.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            inside = line.strip() == "autoscaling:"
            continue
        if inside and ":" in line:
            key, value = line.strip().split(":", 1)
            block[key.strip()] = value.strip()
    return block


def test_scale_down_stabilization_window_is_set():
    block = _autoscaling_block()
    assert "scaleDownStabilizationSeconds" in block
    assert int(block["scaleDownStabilizationSeconds"]) >= 300


def test_autoscaling_bounds_still_present():
    block = _autoscaling_block()
    assert int(block["minReplicas"]) <= int(block["maxReplicas"])
