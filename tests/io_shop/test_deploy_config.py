from __future__ import annotations

import re
from pathlib import Path

"""The scrape config and the deployment have to agree on the metrics port's name.

The ServiceMonitor selects its endpoint by port *name*. A name the Service does
not carry is not an error anywhere - Prometheus simply has no target and the
series stops while the shop goes on serving. So the agreement is checked here,
where a one-sided rename fails the build instead of silently ending collection.
"""

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _without_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def the_metrics_port_name_deployed() -> str:
    lines = (DEPLOY / "values-production.yaml").read_text().splitlines()
    in_metrics = False
    for raw in lines:
        line = _without_comment(raw)
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            in_metrics = line.strip() == "metrics:"
            continue
        if in_metrics:
            match = re.match(r"^\s+portName:\s*['\"]?([^'\"\s]+)['\"]?\s*$", line)
            if match:
                return match.group(1)
    raise AssertionError("values-production.yaml has no metrics.portName")


def the_ports_scraped() -> list[str]:
    text = (DEPLOY / "scrape.yaml").read_text()
    ports = []
    for raw in text.splitlines():
        line = _without_comment(raw)
        match = re.match(r"^\s*-?\s*port:\s*['\"]?([^'\"\s]+)['\"]?\s*$", line)
        if match:
            ports.append(match.group(1))
    return ports


def test_the_scrape_selects_a_port_the_deployment_names() -> None:
    deployed = the_metrics_port_name_deployed()
    scraped = the_ports_scraped()

    assert scraped, "scrape.yaml selects no endpoint port"
    for port in scraped:
        assert port == deployed, (
            f"scrape.yaml selects port {port!r} but values-production.yaml "
            f"names the metrics port {deployed!r}; the scrape would find no target"
        )
