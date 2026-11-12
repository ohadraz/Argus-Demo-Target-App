"""The scrape must select the metrics port by the name the deployment gives it.

The ServiceMonitor in deploy/scrape.yaml picks its endpoint by port *name*; a
name matching no port yields an empty target list rather than an error, so
metrics silently stop. This checks the two files agree.
"""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _strip(line):
    return line.split("#", 1)[0].rstrip()


def _metrics_port_name():
    in_metrics = False
    for raw in (DEPLOY / "values-production.yaml").read_text().splitlines():
        line = _strip(raw)
        if not line:
            continue
        if not line.startswith(" "):
            in_metrics = line == "metrics:"
            continue
        if in_metrics and line.strip().startswith("portName:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    raise AssertionError("metrics.portName not found in values-production.yaml")


def _scrape_port_names():
    names = []
    in_endpoints = False
    endpoints_indent = None
    for raw in (DEPLOY / "scrape.yaml").read_text().splitlines():
        line = _strip(raw)
        if not line:
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if body == "endpoints:":
            in_endpoints = True
            endpoints_indent = indent
            continue
        if in_endpoints and indent <= endpoints_indent and not body.startswith("-"):
            in_endpoints = False
        if in_endpoints:
            body = body.lstrip("-").strip()
            if body.startswith("port:"):
                names.append(body.split(":", 1)[1].strip().strip("'\""))
    return names


def test_scrape_selects_the_metrics_port_by_its_configured_name():
    expected = _metrics_port_name()
    names = _scrape_port_names()
    assert names, "scrape.yaml names no endpoint port"
    for name in names:
        assert name == expected, (
            f"scrape.yaml selects port {name!r} but values-production.yaml "
            f"names the metrics port {expected!r}"
        )
