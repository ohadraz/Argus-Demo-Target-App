"""The ServiceMonitor must scrape the port the deployment actually names.

The scrape selects its endpoint by port *name*; a name that matches no port
silently yields an empty target list. These files are read as plain text so the
check needs no YAML library.
"""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _strip(value):
    value = value.split("#", 1)[0].strip()
    return value.strip("'\"")


def _metrics_port_name():
    lines = (DEPLOY / "values-production.yaml").read_text().splitlines()
    in_metrics = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_metrics = line.split("#", 1)[0].strip() == "metrics:"
            continue
        if in_metrics and line.strip().startswith("portName:"):
            return _strip(line.split(":", 1)[1])
    raise AssertionError("values-production.yaml has no metrics.portName")


def _scraped_port_names():
    lines = (DEPLOY / "scrape.yaml").read_text().splitlines()
    names = []
    in_endpoints = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "endpoints:":
            in_endpoints = True
            continue
        if in_endpoints:
            item = stripped[1:].strip() if stripped.startswith("-") else stripped
            if item.startswith("port:"):
                names.append(_strip(item.split(":", 1)[1]))
    return names


def test_scrape_selects_the_deployed_metrics_port_name():
    port_name = _metrics_port_name()
    scraped = _scraped_port_names()
    assert scraped, "scrape.yaml has no endpoint ports"
    for name in scraped:
        assert name == port_name, (
            f"scrape.yaml selects port {name!r} but values-production.yaml "
            f"names the metrics port {port_name!r}"
        )
