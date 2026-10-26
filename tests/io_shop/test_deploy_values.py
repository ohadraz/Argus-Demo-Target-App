"""The production deployment values point the shop at the cache it has.

This pins the address the summary cache listens on, so a change to it in
deploy/values-production.yaml is a deliberate one rather than a silent one.
"""

from __future__ import annotations

from pathlib import Path

VALUES = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"

# The port the summary cache service listens on.
CACHE_PORT = 6379


def _cache_block() -> dict[str, str]:
    block: dict[str, str] = {}
    inside = False

    for line in VALUES.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.strip() == "cache:"
            continue
        if inside:
            key, _, value = line.strip().partition(":")
            block[key.strip()] = value.strip()

    return block


def test_the_cache_port_is_the_one_the_cache_listens_on() -> None:
    assert _cache_block()["port"] == str(CACHE_PORT)


def test_the_cache_host_is_configured() -> None:
    assert _cache_block()["host"] == "cache.io-shop.svc.cluster.local"
