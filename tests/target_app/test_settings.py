from __future__ import annotations

from pathlib import Path

import pytest

from target_app.settings import VALUES_FILE, the_deployed_cache_endpoint

"""The deployment's own configuration, as the shop reads it.

The cache's address is not in the source - it is in the values file that ships
with the deployment, which is what makes moving it a configuration change
rather than a code change. These pin that it is genuinely read, that a change
to the file changes what the shop dials, and that a missing address is refused
rather than guessed at.
"""


def a_values_file_saying(host: str, port: int, tmp_path: Path) -> Path:
    written = tmp_path / "values-production.yaml"
    written.write_text(
        f"cache:\n  host: {host}\n  port: {port}\n", encoding="utf-8"
    )

    return written


def test_the_shipped_values_file_carries_a_cache_address() -> None:
    # The file the deployment actually ships. A test against a temporary file
    # alone would pass with the real one deleted.
    endpoint = the_deployed_cache_endpoint()

    assert endpoint.host
    assert endpoint.port > 0


def test_the_address_is_the_one_the_file_says(tmp_path: Path) -> None:
    values = a_values_file_saying("cache.elsewhere", 6380, tmp_path)

    endpoint = the_deployed_cache_endpoint(values)

    assert str(endpoint) == "redis://cache.elsewhere:6380"


def test_a_file_with_no_cache_address_is_refused(tmp_path: Path) -> None:
    # A shop that invented an address when its configuration was missing would
    # be a shop whose configuration decides nothing.
    values = tmp_path / "values-production.yaml"
    values.write_text("replicas: 3\n", encoding="utf-8")

    with pytest.raises(KeyError):
        the_deployed_cache_endpoint(values)


def test_the_values_file_sits_outside_the_source_tree() -> None:
    # It is configuration, not code: owned by whoever runs the shop, and
    # changed without the service being rebuilt.
    assert "src" not in VALUES_FILE.parts
    assert VALUES_FILE.exists()
