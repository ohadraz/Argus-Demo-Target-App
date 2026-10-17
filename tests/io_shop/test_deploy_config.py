"""The metrics port name the deployment gives must be the one the scraper selects.

The ServiceMonitor picks the endpoint by port *name*; a name that matches no
port is silently an empty target list and the metrics series just stops.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _strip(value: str) -> str:
    value = value.split("#", 1)[0].strip()
    return value.strip("'\"")


def _values_metrics_port_name() -> str:
    text = (REPO / "deploy" / "values-production.yaml").read_text()
    in_metrics = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            in_metrics = line.split(":", 1)[0].strip() == "metrics"
            continue
        if in_metrics and line.strip().startswith("portName:"):
            return _strip(line.split(":", 1)[1])
    raise AssertionError("metrics.portName not found in values-production.yaml")


def _scrape_endpoint_ports() -> list[str]:
    text = (REPO / "deploy" / "scrape.yaml").read_text()
    ports = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- port:") or stripped.startswith("port:"):
            ports.append(_strip(stripped.split(":", 1)[1]))
    assert ports, "no endpoint port found in scrape.yaml"
    return ports


def test_metrics_port_name_matches_scrape_selector():
    port_name = _values_metrics_port_name()
    assert port_name in _scrape_endpoint_ports(), (
        f"metrics.portName {port_name!r} is not selected by the scrape config "
        f"{_scrape_endpoint_ports()!r}; the metrics series would stop"
    )
