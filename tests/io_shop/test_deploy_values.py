"""The production cache address is the one the cache actually serves on.

The shop stays correct when the cache cannot be reached - it recomputes - so a
wrong port here fails no page and no other test. It only makes every request
slow. This pins the address so that changing it is a deliberate act.
"""

from pathlib import Path

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"


def _block(name: str) -> dict[str, str]:
    """The `key: value` pairs indented under a top-level `name:` block."""
    fields: dict[str, str] = {}
    inside = False
    for line in VALUES.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.strip() == f"{name}:"
            continue
        if inside:
            key, _, value = line.strip().partition(":")
            fields[key.strip()] = value.strip()
    return fields


def test_production_cache_points_at_the_port_the_cache_serves_on():
    cache = _block("cache")

    assert cache["host"] == "cache.io-shop.svc.cluster.local"
    assert cache["port"] == "6379"
