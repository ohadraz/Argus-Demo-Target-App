from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from io_shop.endpoints import the_shops_routes

"""The routes the shop answers about itself.

What they report is handed in, so these pin down the wiring rather than the
figures: each route answers with what it was given, and the exposition goes out
under the content type it was given rather than one the shop assumes.
"""

SOME_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def a_shop_answering_with(log_lines: list[str], exposition: str) -> TestClient:
    shop = FastAPI()
    shop.include_router(the_shops_routes(
        log_lines=lambda: log_lines,
        exposition=lambda: exposition,
        exposition_content_type=SOME_CONTENT_TYPE
    ))
    return TestClient(shop)


def test_health_says_the_shop_is_up() -> None:
    client = a_shop_answering_with(log_lines=[], exposition="")

    assert client.get("/health").json() == {"status": "ok"}


def test_logs_are_the_lines_handed_in() -> None:
    log_lines = ["first line", "second line"]
    client = a_shop_answering_with(log_lines=log_lines, exposition="")

    assert client.get("/logs").json() == log_lines


def test_metrics_are_the_exposition_handed_in() -> None:
    exposition = "io_shop_requests_total 42\n"
    client = a_shop_answering_with(log_lines=[], exposition=exposition)

    scraped = client.get("/metrics")

    assert scraped.text == exposition
    assert scraped.headers["content-type"] == SOME_CONTENT_TYPE
