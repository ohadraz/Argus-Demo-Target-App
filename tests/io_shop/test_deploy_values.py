"""The production deployment points the shop at the cache where it actually is.

The shop survives a wrong cache address by design - every page falls back to
working its figure out - which is exactly why a wrong address does not show up
as an error anywhere. It shows up as every page being slow. So the address is
pinned here, against the one io-summary-cache serves on, and moving it has to
be a deliberate change made alongside the cache rather than a config edit that
ships on its own.
"""

from __future__ import annotations

from pathlib import Path

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"

# Where io-summary-cache listens.
CACHE_HOST = "cache.io-shop.svc.cluster.local"
CACHE_PORT = 6379


def _section(name: str) -> dict[str, str]:
    """The `key: value` pairs directly under a top-level section."""
    values: dict[str, str] = {}
    inside = False

    for line in VALUES.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.rstrip() == f"{name}:"
            continue
        if inside:
            key, _, value = line.strip().partition(":")
            values[key.strip()] = value.strip()

    return values


def test_production_cache_port_is_the_port_the_cache_serves_on() -> None:
    assert int(_section("cache")["port"]) == CACHE_PORT


def test_production_cache_host_is_the_cache_service() -> None:
    assert _section("cache")["host"] == CACHE_HOST
