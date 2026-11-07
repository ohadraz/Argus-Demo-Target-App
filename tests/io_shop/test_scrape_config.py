"""The ServiceMonitor must scrape the port name the deployment actually gives.

The scrape selects its endpoint by port *name*. A name that matches no port is
not an error anywhere - Prometheus just ends up with no targets and the metrics
stop silently. So the two files are checked against each other here.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "deploy" / "values-production.yaml"
SCRAPE = REPO_ROOT / "deploy" / "scrape.yaml"


def _strip(value):
    value = value.split("#", 1)[0].strip()
    return value.strip("'\"")


def _configured_port_name():
    in_metrics = False
    for raw in VALUES.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not raw.startswith((" ", "\t")):
            in_metrics = line.strip() == "metrics:"
            continue
        if in_metrics and line.strip().startswith("portName:"):
            return _strip(line.strip()[len("portName:"):])
    raise AssertionError("metrics.portName not found in values-production.yaml")


def _scraped_port_names():
    names = []
    in_endpoints = False
    endpoints_indent = None
    for raw in SCRAPE.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if stripped == "endpoints:":
            in_endpoints = True
            endpoints_indent = indent
            continue
        if in_endpoints and indent <= endpoints_indent and not stripped.startswith("-"):
            in_endpoints = False
        if in_endpoints:
            item = stripped[1:].strip() if stripped.startswith("-") else stripped
            if item.startswith("port:"):
                names.append(_strip(item[len("port:"):]))
    return names


def test_scrape_selects_the_configured_metrics_port_name():
    configured = _configured_port_name()
    scraped = _scraped_port_names()
    assert scraped, "the ServiceMonitor scrapes no named port"
    for name in scraped:
        assert name == configured, (
            f"scrape.yaml selects port {name!r} but values-production.yaml "
            f"names the metrics port {configured!r}"
        )
