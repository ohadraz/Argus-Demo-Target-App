"""The scrape config must select the metrics port by the name the deployment gives it.

The ServiceMonitor in deploy/scrape.yaml selects the endpoint by port *name*,
and deploy/values-production.yaml is what names the port. If the two disagree
nothing errors: Prometheus simply has no target and the series stops.
"""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _strip(value):
    value = value.split("#", 1)[0].strip()
    return value.strip("'\"")


def _values_metrics_port_name():
    in_metrics = False
    for line in (DEPLOY / "values-production.yaml").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_metrics = line.split(":", 1)[0].strip() == "metrics"
            continue
        if in_metrics and line.strip().startswith("portName:"):
            return _strip(line.split(":", 1)[1])
    raise AssertionError("metrics.portName not found in values-production.yaml")


def _scrape_endpoint_ports():
    ports = []
    for line in (DEPLOY / "scrape.yaml").read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- port:") or stripped.startswith("port:"):
            ports.append(_strip(stripped.split(":", 1)[1]))
    return ports


def test_scrape_selects_the_port_name_the_deployment_publishes():
    name = _values_metrics_port_name()
    ports = _scrape_endpoint_ports()
    assert ports, "scrape.yaml selects no endpoint port"
    for port in ports:
        assert port == name, (
            f"scrape.yaml selects port {port!r} but values-production.yaml "
            f"names the metrics port {name!r}; nothing would be scraped"
        )
