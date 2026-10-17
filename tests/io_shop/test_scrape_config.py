"""The scrape selects the metrics port by name; that name must be the deployed one.

A mismatch is not an error anywhere - the scraper just finds no targets and the
series stops - so it is checked here instead.
"""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _strip(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def _metrics_port_name() -> str:
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


def _scrape_ports() -> list[str]:
    ports = []
    for raw in (DEPLOY / "scrape.yaml").read_text().splitlines():
        line = _strip(raw).strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if line.startswith("port:"):
            ports.append(line.split(":", 1)[1].strip().strip("'\""))
    return ports


def test_scrape_selects_the_deployed_metrics_port_name() -> None:
    ports = _scrape_ports()
    assert ports, "scrape.yaml selects no endpoint port"
    expected = _metrics_port_name()
    assert all(port == expected for port in ports), (ports, expected)
