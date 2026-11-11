"""The ServiceMonitor must scrape the port name the deployment gives the port.

Prometheus selects the scrape endpoint by port *name*; a name that matches no
port yields an empty target list rather than an error, so the shop keeps serving
while its metrics silently stop. These two files must agree.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "deploy" / "values-production.yaml"
SCRAPE = REPO_ROOT / "deploy" / "scrape.yaml"


def _strip_comment(line):
    return line.split("#", 1)[0].rstrip()


def _metrics_port_name():
    in_metrics = False
    for raw in VALUES.read_text().splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if not line.startswith(" "):
            in_metrics = line.strip() == "metrics:"
            continue
        if in_metrics:
            m = re.match(r"^\s+portName:\s*['\"]?([^'\"\s]+)['\"]?\s*$", line)
            if m:
                return m.group(1)
    raise AssertionError("metrics.portName not found in values-production.yaml")


def _scrape_endpoint_ports():
    ports = []
    in_endpoints = False
    endpoints_indent = None
    for raw in SCRAPE.read_text().splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if line.strip() == "endpoints:":
            in_endpoints = True
            endpoints_indent = indent
            continue
        if in_endpoints and indent <= endpoints_indent and not line.lstrip().startswith("-"):
            in_endpoints = False
        if in_endpoints:
            m = re.match(r"^\s*(?:-\s+)?port:\s*['\"]?([^'\"\s]+)['\"]?\s*$", line)
            if m:
                ports.append(m.group(1))
    return ports


def test_scrape_endpoints_select_the_deployed_metrics_port_name():
    port_name = _metrics_port_name()
    ports = _scrape_endpoint_ports()
    assert ports, "scrape.yaml declares no endpoint ports"
    for port in ports:
        assert port == port_name, (
            f"scrape.yaml scrapes port {port!r} but values-production.yaml "
            f"names the metrics port {port_name!r}; Prometheus would find no targets"
        )
