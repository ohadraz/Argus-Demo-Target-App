from __future__ import annotations

import re
from pathlib import Path

"""Whether the scrape config still finds the port the deployment names.

The ServiceMonitor selects the metrics endpoint by port *name*. A name that
matches no port is silently an empty target list, so metrics stop with no
error anywhere. This keeps deploy/scrape.yaml and
deploy/values-production.yaml in step.
"""

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _metrics_port_name() -> str:
    text = (DEPLOY / "values-production.yaml").read_text()
    block = re.search(r"^metrics:\s*\n((?:[ \t]+.*\n?|\s*\n)+)", text, re.MULTILINE)
    assert block, "values-production.yaml has no metrics: block"
    name = re.search(r"^\s+portName:\s*['\"]?([^'\"\s#]+)", block.group(1), re.MULTILINE)
    assert name, "metrics: block has no portName"
    return name.group(1)


def _scraped_port_names() -> list[str]:
    text = (DEPLOY / "scrape.yaml").read_text()
    _, _, endpoints = text.partition("endpoints:")
    return re.findall(r"^\s*-?\s*port:\s*['\"]?([^'\"\s#]+)", endpoints, re.MULTILINE)


def test_the_scrape_selects_the_port_name_the_deployment_gives_metrics() -> None:
    scraped = _scraped_port_names()

    assert scraped, "scrape.yaml selects no endpoint port"
    assert _metrics_port_name() in scraped
