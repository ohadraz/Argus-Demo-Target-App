from __future__ import annotations

from pathlib import Path

"""The scrape config and the deployment agree on the metrics port's name.

The ServiceMonitor selects the shop's metrics endpoint by port *name*. A name
that matches no port is not an error anywhere - Prometheus simply has no target
and the shop's metrics stop. Renaming the port in one file and not the other is
exactly how that happened, so this checks the two files against each other.
"""

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "deploy" / "values-production.yaml"
SCRAPE = REPO_ROOT / "deploy" / "scrape.yaml"


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def _metrics_port_name() -> str:
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
    names: list[str] = []
    in_endpoints = False
    endpoints_indent = 0
    for raw in SCRAPE.read_text().splitlines():
        line = _strip_comment(raw)
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
            entry = stripped.lstrip("- ").strip()
            if entry.startswith("port:"):
                names.append(entry.split(":", 1)[1].strip().strip("'\""))
    return names


def test_the_scrape_selects_the_port_name_the_deployment_gives_the_metrics_port() -> None:
    port_name = _metrics_port_name()
    scraped = _scraped_port_names()

    assert scraped, "the ServiceMonitor names no endpoint port"
    assert all(name == port_name for name in scraped), (
        f"scrape.yaml selects {scraped} but values-production.yaml names the "
        f"metrics port {port_name!r}"
    )
