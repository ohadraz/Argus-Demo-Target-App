"""The scrape config must select the metrics port by the name the deployment gives it.

Prometheus selects the endpoint by port *name*; a name matching no port is not
an error, just an empty target list and no samples. These two files are edited
independently, so this test is what keeps them agreeing.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "deploy" / "values-production.yaml"
SCRAPE = REPO_ROOT / "deploy" / "scrape.yaml"


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def _configured_metrics_port_name() -> str:
    in_metrics = False
    for raw in VALUES.read_text().splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            in_metrics = line.strip() == "metrics:"
            continue
        if in_metrics and line.strip().startswith("portName:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    raise AssertionError("metrics.portName not found in values-production.yaml")


def _scraped_port_names() -> list[str]:
    names = []
    in_endpoints = False
    for raw in SCRAPE.read_text().splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "endpoints:":
            in_endpoints = True
            continue
        if in_endpoints:
            item = stripped[2:].strip() if stripped.startswith("- ") else stripped
            if item.startswith("port:"):
                names.append(item.split(":", 1)[1].strip().strip("'\""))
    return names


def test_scrape_selects_the_configured_metrics_port_name():
    configured = _configured_metrics_port_name()
    scraped = _scraped_port_names()
    assert scraped, "scrape.yaml selects no endpoint port"
    for name in scraped:
        assert name == configured, (
            f"scrape.yaml selects port {name!r} but values-production.yaml "
            f"names the metrics port {configured!r}"
        )
