from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import PurePath

from io_shop.accounts import Account
from io_shop.monthly_statement import (
    MonthlyStatement,
    StatementPeriod,
    render_monthly_statement,
)
from io_shop.payment_provider import AskTheProvider, card_on_file
from io_shop.spend_summary import render_spend_summary
from io_shop.summary_cache import (
    CacheEndpoint,
    CacheUnreachable,
    LookUpSummary,
    cached_summary,
)
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

    `statement` is the monthly statement panel, on the pages the newest rollout
    reached and absent everywhere else. It sits beside the figure rather than
    replacing it: the statement is a second thing the page shows, so a request
    outside the rollout renders the page it always rendered rather than a
    shorter one.

    `served_from_cache` says whether the figure was read rather than worked out.
    It is reported on a page that rendered perfectly well, because it is the
    difference between the two ways of rendering perfectly well - and the share
    of requests taking each is what says whether the shop's fast path is there.

    `cache_failure` carries the words of a cache that could not be reached. It
    sits beside `failure` rather than in it, and the distinction is the whole
    scenario: this is a page that *succeeded* while something underneath it was
    broken, so a reader sees it in the logs without seeing it in the error rate.
    """

    figure_cents: int | None
    card_last_four: str | None
    failure: str | None
    served_from_cache: bool = False
    cache_failure: str | None = None
    statement: MonthlyStatement | None = None


def serve_account_page(account: Account,
                       use_monthly_summary: bool,
                       ask_the_provider: AskTheProvider,
                       look_up_summary: LookUpSummary | None = None,
                       cache_endpoint: CacheEndpoint | None = None,
                       use_typical_spend: bool = False,
                       use_monthly_statement: bool = False,
                       statement_period: StatementPeriod | None = None
                       ) -> RenderedPage:
    """Renders the account page, reporting a failure rather than raising one.

    Two things are shown and both are needed: what the shopper averages per
    item, which Io works out for itself, and the card it would charge, which
    only the payment provider knows. The second is a call to another company
    from inside a page render, which is ordinary and is also why an outage over
    there arrives here as Io's own error rate.

    `use_monthly_summary` and `use_typical_spend` are the rollout decisions
    already made - whether this request is one of the ones each new figure is
    live for. The page does not make those decisions itself; it is told, the way
    a handler is told by the flag SDK that evaluated for this user. Two of them
    because there are two rollouts running, and a page carrying one boolean for
    both would be a page that cannot say which feature a request got.

    `look_up_summary` and `cache_endpoint` are how the figure is looked for
    before it is worked out. Both optional and both absent together, because a
    deployment that configured no cache has none - and a page that insisted on
    one would make an optimisation into a requirement, which is the very thing
    this shop's cache is not.

    `use_monthly_statement` is the third rollout decision, and it arrives here
    already made like the other two. `statement_period` is which month the
    request is asking about, which the page is told rather than deriving: the
    shop's purchase records carry a month flag and no date, so the only thing
    here that knows the calendar is whoever handled the request.
    """
    try:
        figure_cents, from_cache, cache_failure = _the_figure_for(
            account, use_monthly_summary, look_up_summary, cache_endpoint,
            use_typical_spend
        )
        statement = _the_statement_for(
            account, use_monthly_statement, statement_period
        )
        card = card_on_file(account.shopper_id, ask_the_provider)
    except Exception as error:  # noqa: BLE001 - the boundary records anything
        failure = f"{type(error).__name__}: {error} at {_where_it_was_raised(error)}"
        record_visit(account.shopper_id, failure)

        return RenderedPage(figure_cents=None, card_last_four=None, failure=failure)

    record_visit(account.shopper_id, str(figure_cents))

    return RenderedPage(figure_cents=figure_cents,
                        card_last_four=card.last_four,
                        failure=None,
                        served_from_cache=from_cache,
                        cache_failure=cache_failure,
                        statement=statement)


def _the_statement_for(account: Account,
                       use_monthly_statement: bool,
                       period: StatementPeriod | None) -> MonthlyStatement | None:
    """The monthly statement panel, where this request is one the rollout
    reached, and nothing where it is not.

    Nothing also where the request arrived without a period. That is a
    misconfigured caller rather than a shopper's month, and rendering a
    statement titled with a month nobody named would put a wrong heading over
    correct figures - see `io_shop.monthly_statement.StatementPeriod`, which is
    where that argument is made at length. A missing panel is visible to
    whoever configured the rollout; a wrongly titled one is visible only to the
    shopper.
    """
    if not use_monthly_statement or period is None:
        return None

    return render_monthly_statement(account, period)


def _the_figure_for(
    account: Account,
    use_monthly_summary: bool,
    look_up_summary: LookUpSummary | None,
    cache_endpoint: CacheEndpoint | None,
    use_typical_spend: bool
) -> tuple[int, bool, str | None]:
    """The figure to show, whether it came from the cache, and what the cache
    said if it could not be reached.

    The cache is asked first and is allowed to fail. Whatever it does - answers
    with a figure, answers with nothing, or cannot be reached at all - this
    returns a figure, because the shop can always work one out. That is the
    fallback the whole scenario rests on: the page is correct either way, and
    the only thing the cache decides is how long getting here took.

    A cache failure is returned rather than raised onward, because it is not
    this request's failure. The request succeeded.
    """
    if look_up_summary is None or cache_endpoint is None:
        return render_spend_summary(
            account,
            use_monthly_summary=use_monthly_summary,
            use_typical_spend=use_typical_spend
        ), False, None

    cache_failure: str | None = None

    try:
        found = cached_summary(account.shopper_id, look_up_summary, cache_endpoint)
    except CacheUnreachable as unreachable:
        found, cache_failure = None, str(unreachable)

    if found is not None:
        return found, True, cache_failure

    return render_spend_summary(
        account,
        use_monthly_summary=use_monthly_summary,
        use_typical_spend=use_typical_spend
    ), False, cache_failure


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
