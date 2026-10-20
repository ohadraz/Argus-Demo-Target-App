"""The scrape config must select the metrics port by the name the deploy gives it.

A ServiceMonitor endpoint whose port name matches no port on the Service is not
an error anywhere: the target list is simply empty and the series stops. These
two files are the only places the name lives, so check they agree.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "deploy" / "values-production.yaml"
SCRAPE = REPO_ROOT / "deploy" / "scrape.yaml"


def _strip(line):
    return line.split("#", 1)[0].rstrip()


def _indent(line):
    return len(line) - len(line.lstrip())


def _value(line):
    return line.split(":", 1)[1].strip().strip("'\"")


def _metrics_port_name():
    in_metrics = False
    for raw in VALUES.read_text().splitlines():
        line = _strip(raw)
        if not line.strip():
            continue
        if _indent(line) == 0:
            in_metrics = line.strip() == "metrics:"
            continue
        if in_metrics and line.strip().startswith("portName:"):
            return _value(line)
    raise AssertionError("metrics.portName not found in values-production.yaml")


def _scrape_port_names():
    names = []
    in_endpoints = False
    endpoints_indent = None
    for raw in SCRAPE.read_text().splitlines():
        line = _strip(raw)
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "endpoints:":
            in_endpoints = True
            endpoints_indent = _indent(line)
            continue
        if in_endpoints:
            if _indent(line) <= endpoints_indent and not stripped.startswith("-"):
                in_endpoints = False
                continue
            item = stripped[1:].strip() if stripped.startswith("-") else stripped
            if item.startswith("port:"):
                names.append(_value(item))
    assert names, "no endpoint ports found in scrape.yaml"
    return names


def test_scrape_selects_the_metrics_port_by_its_deployed_name():
    port_name = _metrics_port_name()
    for selected in _scrape_port_names():
        assert selected == port_name, (
            f"scrape.yaml selects port {selected!r} but values-production.yaml "
            f"names the metrics port {port_name!r}; nothing would be scraped"
        )
