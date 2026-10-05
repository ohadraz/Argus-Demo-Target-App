from __future__ import annotations

import re
from pathlib import Path

"""The contract between the two deploy files.

The scrape config selects its target by the port's *name*, and the name it has
to use is the one `metrics.portName` in values-production.yaml gives the port.
Nothing in the cluster reports a mismatch: a ServiceMonitor endpoint naming a
port no Service exposes yields an empty target list, so the shop keeps serving
and answering normally while its series silently stops.

These read both documents with a small line scan rather than a YAML parser, so
the check costs the repository no dependency it does not already have.
"""

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _lines(name: str) -> list[str]:
    text = (DEPLOY / name).read_text(encoding="utf-8")
    return [line.split("#", 1)[0].rstrip() for line in text.splitlines()]


def the_port_name_the_deployment_gives() -> str:
    """`metrics.portName` from values-production.yaml."""
    in_metrics_block = False
    for line in _lines("values-production.yaml"):
        if not line.strip():
            continue
        if re.match(r"^metrics:\s*$", line):
            in_metrics_block = True
            continue
        if in_metrics_block:
            if not line.startswith((" ", "\t")):
                break
            found = re.match(r"^\s+portName:\s*(\S+)\s*$", line)
            if found:
                return found.group(1).strip("'\"")
    raise AssertionError("values-production.yaml names no metrics.portName")


def the_port_names_the_scraper_selects() -> list[str]:
    """Every `port:` named by an endpoint in scrape.yaml."""
    selected = [
        found.group(1).strip("'\"")
        for found in (
            re.match(r"^\s*-?\s*port:\s*(\S+)\s*$", line)
            for line in _lines("scrape.yaml")
        )
        if found
    ]
    assert selected, "scrape.yaml selects no port by name"
    return selected


def the_scrape_intervals() -> list[int]:
    return [
        int(found.group(1))
        for found in (
            re.match(r"^\s*interval:\s*(\d+)s\s*$", line)
            for line in _lines("scrape.yaml")
        )
        if found
    ]


def test_the_scraper_selects_the_port_the_deployment_names() -> None:
    deployed = the_port_name_the_deployment_gives()

    for selected in the_port_names_the_scraper_selects():
        assert selected == deployed, (
            f"scrape.yaml scrapes port {selected!r} but the deployment names "
            f"the metrics port {deployed!r}: the target list would be empty "
            "and no sample would be collected"
        )


def test_the_metrics_port_name_follows_the_platform_convention() -> None:
    # `<protocol>[-<purpose>]`, applied estate-wide - the rename that broke the
    # scrape. Pinned so conforming to it cannot be mistaken for the fault again.
    name = the_port_name_the_deployment_gives()
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)


def test_the_scrape_is_at_least_as_often_as_a_sample_is_expected() -> None:
    # The alert fires on a minute with no sample, so scraping may not be slower.
    for interval in the_scrape_intervals():
        assert interval <= 60
