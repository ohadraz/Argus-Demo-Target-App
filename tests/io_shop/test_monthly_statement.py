from __future__ import annotations

import pytest
from io_shop.accounts import Account, Purchase
from io_shop.monthly_statement import (
    STATEMENT_COLUMNS,
    as_csv_rows,
    as_html,
    as_plain_text,
    compare_statements,
    describe_month,
    period_for,
    printed_shares,
    problems_with,
    reconciles,
    render_monthly_statement,
    statement_rows,
    statement_sections,
)

"""Io's monthly statement panel - the month laid out rather than summed.

A safety net rather than a specification: the panel was written first and these
cover what would be expensive to find out from a shopper. The one that matters
most is the empty month, because that is the fault the
`monthly-statement-panel` scenario stages, and a change that quietly made it
stop raising would leave that scenario staging nothing at all.
"""

MARCH = period_for(3, 2026)


def a_shopper_who_bought_this_month() -> Account:
    return Account(
        shopper_id="shopper-with-a-month",
        purchases=(
            Purchase(price_cents=1200, in_current_month=True, category="Books"),
            Purchase(price_cents=6400, in_current_month=True, category="Home"),
            Purchase(price_cents=3300, in_current_month=True, category="Garden"),
            Purchase(price_cents=900, in_current_month=False, category="Books"),
        ),
        total_cents=11800,
        total_this_month_cents=10900,
    )


def a_shopper_who_bought_nothing_this_month() -> Account:
    return Account(
        shopper_id="shopper-idle-this-month",
        purchases=(Purchase(price_cents=900, in_current_month=False),),
        total_cents=900,
        total_this_month_cents=0,
    )


def test_the_statement_reports_the_month_it_was_asked_for() -> None:
    statement = render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)

    assert statement.period.title == "March 2026"
    assert statement.headline_cents == 10900
    assert statement.purchase_count == 3


def test_the_statement_takes_its_shape_from_this_month_alone() -> None:
    # The purchase outside the month is cheaper than everything in it, so a
    # statement that had counted it would report it as the smallest.
    statement = render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)

    assert statement.biggest_cents == 6400
    assert statement.smallest_cents == 1200


def test_a_month_with_nothing_in_it_fails_rather_than_reporting_zero() -> None:
    # The fault the `monthly-statement-panel` scenario stages. A statement
    # reporting a made-up zero would be a panel nobody could trust on the
    # months it can describe.
    with pytest.raises(ValueError):
        render_monthly_statement(a_shopper_who_bought_nothing_this_month(), MARCH)


def test_the_breakdowns_add_up_to_the_headline() -> None:
    statement = render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)

    assert reconciles(statement)
    assert problems_with(statement) == []


def test_a_column_of_percentages_comes_to_a_hundred() -> None:
    # Three thirds rounded one at a time print as 99%, which is the complaint
    # every statement in the world has received at least once.
    assert sum(printed_shares([1 / 3, 1 / 3, 1 / 3])) == 100


def test_an_empty_column_apportions_nothing() -> None:
    assert printed_shares([]) == []


def test_there_is_no_thirteenth_month() -> None:
    with pytest.raises(ValueError):
        period_for(13, 2026)


def test_every_section_that_is_printed_has_rows_in_it() -> None:
    sections = statement_sections(
        render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)
    )

    assert sections
    assert all(section.rows for section in sections)


def test_the_flat_rows_are_the_sections_flattened() -> None:
    statement = render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)
    from_sections = [
        row for section in statement_sections(statement) for row in section.rows
    ]

    assert statement_rows(statement) == from_sections


def test_the_export_leads_with_its_header() -> None:
    statement = render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)

    assert as_csv_rows(statement)[0] == STATEMENT_COLUMNS


def test_the_download_is_named_after_the_month() -> None:
    assert MARCH.as_a_filename == "io-statement-2026-march.csv"


def test_the_email_is_titled_with_the_month() -> None:
    statement = render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)

    assert as_plain_text(statement).startswith("MARCH 2026")


def test_the_markup_escapes_a_category_the_catalogue_made_up() -> None:
    # Categories come from a catalogue somebody else edits, which is why
    # nothing here is trusted to be markup-safe.
    a_shopper_whose_category_is_markup = Account(
        shopper_id="shopper-with-an-odd-category",
        purchases=(
            Purchase(
                price_cents=500, in_current_month=True, category="<script>"
            ),
        ),
        total_cents=500,
        total_this_month_cents=500,
    )

    markup = as_html(
        render_monthly_statement(a_shopper_whose_category_is_markup, MARCH)
    )

    assert "<script>" not in markup


def test_a_month_is_described_in_one_sentence() -> None:
    statement = render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)

    assert describe_month(statement).endswith(".")


def test_two_identical_months_are_reported_as_identical() -> None:
    statement = render_monthly_statement(a_shopper_who_bought_this_month(), MARCH)

    assert "the same as last month" in compare_statements(statement, statement)
