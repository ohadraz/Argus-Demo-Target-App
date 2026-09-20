from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import PurePath

from io_shop.accounts import Account
from io_shop.payment_provider import AskTheProvider, card_on_file
from io_shop.spend_summary import render_spend_summary
from io_shop.visits import record_visit

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

    Either the page rendered, in which case both of the things it shows are
    here, or it failed, in which case neither is and `failure` carries the
    error's own words and the line they were raised on - because those are what
    reach the log and what a reader diagnoses from. A generic "request failed"
    would describe every incident equally, and the error's words alone name a
    fault without naming where it lives.
    """

    figure_cents: int | None
    card_last_four: str | None
    failure: str | None


def serve_account_page(account: Account,
                       use_monthly_summary: bool,
                       ask_the_provider: AskTheProvider) -> RenderedPage:
    """Renders the account page, reporting a failure rather than raising one.

    Two things are shown and both are needed: what the shopper averages per
    item, which Io works out for itself, and the card it would charge, which
    only the payment provider knows. The second is a call to another company
    from inside a page render, which is ordinary and is also why an outage over
    there arrives here as Io's own error rate.

    `use_monthly_summary` is the rollout decision already made - whether this
    request is one of the ones the new figure is live for. The page does not
    make that decision itself; it is told, the way a handler is told by the flag
    SDK that evaluated for this user.
    """
    try:
        figure_cents = render_spend_summary(
            account, use_monthly_summary=use_monthly_summary
        )
        card = card_on_file(account.shopper_id, ask_the_provider)
    except Exception as error:  # noqa: BLE001 - the boundary records anything
        failure = f"{type(error).__name__}: {error} at {_where_it_was_raised(error)}"
        record_visit(account.shopper_id, failure)

        return RenderedPage(figure_cents=None, card_last_four=None, failure=failure)

    record_visit(account.shopper_id, str(figure_cents))

    return RenderedPage(figure_cents=figure_cents,
                        card_last_four=card.last_four,
                        failure=None)


def _where_it_was_raised(error: BaseException) -> str:
    """The innermost frame, as `path/to/file.py:line`.

    The innermost rather than the boundary's own, because the boundary is where
    every failure is caught and is therefore the same answer for all of them.
    What a reader wants is the line that actually divided by zero.

    Said as a repository path rather than the absolute one the interpreter
    reports, so that the log names a file somebody can open. The frame's path is
    wherever this happens to be installed - a container's site-packages, an
    editable checkout - and none of those spellings exist in the repository.
    """
    frames = traceback.extract_tb(error.__traceback__)

    if not frames:
        return "an unknown line"

    innermost = frames[-1]

    return f"{_as_a_repository_path(innermost.filename)}:{innermost.lineno}"


def _as_a_repository_path(filename: str) -> str:
    """The file as the repository spells it, if it can be recognised.

    Cut at the package directory and put back under `src/`, which is where this
    shop keeps its source. Anything unrecognisable is reported whole rather than
    guessed at - a wrong path in a log is worse than a long one, because it
    sends a reader to a file that does not exist and looks authoritative doing
    it.
    """
    parts = PurePath(filename).parts

    if _PACKAGE not in parts:
        return filename

    return "/".join(("src", *parts[parts.index(_PACKAGE):]))


_PACKAGE = "io_shop"
