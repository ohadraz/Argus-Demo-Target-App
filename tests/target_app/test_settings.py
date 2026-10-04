from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from target_app.settings import (
    VALUES_FILE,
    PrometheusSettings,
    the_declared_autoscaler,
    the_deployed_cache_endpoint,
)

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


def a_values_file_declaring(floor: int,
                            ceiling: int,
                            stabilization: int,
                            tmp_path: Path) -> Path:
    written = tmp_path / "values-production.yaml"
    written.write_text(
        "autoscaling:\n"
        f"  minReplicas: {floor}\n"
        f"  maxReplicas: {ceiling}\n"
        "  targetCpuPercent: 70\n"
        f"  scaleDownStabilizationSeconds: {stabilization}\n",
        encoding="utf-8"
    )

    return written


def test_the_shipped_values_file_declares_an_autoscaler() -> None:
    # The file the deployment actually ships, as the cache's address is checked
    # against it: a test against a temporary file alone would pass with the real
    # stanza deleted.
    autoscaler = the_declared_autoscaler()

    assert autoscaler.min_replicas > 0
    assert autoscaler.max_replicas > autoscaler.min_replicas


def test_the_shipped_autoscaler_is_the_one_that_flaps() -> None:
    # The fault this deployment carries, and the reason the scenario has
    # something for a patch to correct. Kubernetes defaults this to 300; zero is
    # a controller acting on a reading its own last action produced.
    assert the_declared_autoscaler().scale_down_stabilization_seconds == 0


def test_the_bounds_are_the_ones_the_file_says(tmp_path: Path) -> None:
    values = a_values_file_declaring(2, 9, 120, tmp_path)

    autoscaler = the_declared_autoscaler(values)

    assert autoscaler.min_replicas == 2
    assert autoscaler.max_replicas == 9
    assert autoscaler.scale_down_stabilization_seconds == 120


def test_a_file_with_no_autoscaler_is_refused(tmp_path: Path) -> None:
    # A shop that invented an autoscaler when its configuration was missing
    # would be a shop whose configuration decides nothing.
    values = tmp_path / "values-production.yaml"
    values.write_text("replicas: 3\n", encoding="utf-8")

    with pytest.raises(KeyError):
        the_declared_autoscaler(values)


def test_the_prometheus_stand_in_serves_the_minute_in_progress_by_default(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0 is every push's run, which must stay as fast as the shop always was.
    monkeypatch.delenv("PROMETHEUS_REPORTING_LAG_MINUTES", raising=False)

    assert PrometheusSettings().reporting_lag_minutes == 0


def test_the_prometheus_stand_in_refuses_a_lag_it_cannot_serve() -> None:
    # A minute either is reported while it runs or once it is over; two minutes
    # late is a source nothing here stands in for.
    with pytest.raises(ValidationError):
        PrometheusSettings(reporting_lag_minutes=2)
