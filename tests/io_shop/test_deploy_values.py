"""The production deployment points the summary cache where the cache listens.

A wrong cache address here has to be caught by this test, so it is caught in
review rather than after a deploy.
"""

from __future__ import annotations

from pathlib import Path

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"

# The port the summary cache serves on.
CACHE_PORT = 6379


def _cache_block() -> dict[str, str]:
    block: dict[str, str] = {}
    inside = False

    for line in VALUES.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.rstrip() == "cache:"
            continue
        if inside:
            key, _, value = line.strip().partition(":")
            block[key.strip()] = value.strip()

    return block


def test_production_cache_port_is_the_port_the_cache_listens_on():
    cache = _cache_block()

    assert int(cache["port"]) == CACHE_PORT


def test_production_cache_host_is_the_cache_service():
    cache = _cache_block()

    assert cache["host"] == "cache.io-shop.svc.cluster.local"
