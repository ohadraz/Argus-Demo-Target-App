from __future__ import annotations

from dataclasses import dataclass

from io_shop.accounts import Account
from io_shop.spend_summary import render_spend_summary

"""Serving one account page - the shop's request boundary.

This is the line at which an exception stops being a bug and becomes an
incident: everything below it raises, and here it is caught, recorded in the
shop's own words, and turned into a failed response. It is the only place in the
shop that catches broadly, and it does so on purpose - a request handler that let
an unexpected error escape would take the worker with it instead of reporting a
rate somebody can alert on.
"""


@dataclass(frozen=True)
class RenderedPage:
    """How serving one account page went.

    Exactly one of the two is set. `failure` carries the error's own words,
    because those words are what reaches the log and what a reader diagnoses
    from - a generic "request failed" would describe every incident equally.
    """

    figure_cents: int | None
    failure: str | None


def serve_account_page(account: Account, use_monthly_summary: bool) -> RenderedPage:
    """Renders the account page's spend figure, reporting a failure rather than
    raising one.

    `use_monthly_summary` is the rollout decision already made - whether this
    request is one of the ones the new figure is live for. The page does not
    make that decision itself; it is told, the way a handler is told by the flag
    SDK that evaluated for this user.
    """
    try:
        return RenderedPage(
            figure_cents=render_spend_summary(
                account, use_monthly_summary=use_monthly_summary
            ),
            failure=None,
        )
    except Exception as error:  # noqa: BLE001 - the boundary records anything
        return RenderedPage(
            figure_cents=None, failure=f"{type(error).__name__}: {error}"
        )
