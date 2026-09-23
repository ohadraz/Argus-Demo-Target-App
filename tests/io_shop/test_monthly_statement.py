from io_shop.accounts import Account, Purchase
from io_shop.monthly_statement import (
    as_csv_rows,
    as_html,
    as_plain_text,
    describe_month,
    period_for,
    problems_with,
    reconciles,
    render_monthly_statement,
    statement_rows,
    statement_sections,
    the_biggest_purchase_this_month,
    the_mean_purchase_this_month,
    the_smallest_purchase_this_month,
)

SEPTEMBER = period_for(9, 2026)


def an_account(purchases, total_cents, total_this_month_cents):
    return Account(
        shopper_id="shopper-1",
        purchases=tuple(purchases),
        total_cents=total_cents,
        total_this_month_cents=total_this_month_cents
    )


def a_shopper_who_bought_nothing_this_month():
    """A perfectly ordinary account on the first week of the month."""
    return an_account(
        purchases=[
            Purchase(price_cents=4_000, in_current_month=False, category="Books"),
            Purchase(price_cents=6_000, in_current_month=False, category="Home")
        ],
        total_cents=10_000,
        total_this_month_cents=0
    )


def a_shopper_with_a_month():
    return an_account(
        purchases=[
            Purchase(price_cents=300, in_current_month=True, category="Groceries"),
            Purchase(
                price_cents=7_500,
                in_current_month=True,
                category="Electronics",
                delivery_cents=499
            ),
            Purchase(price_cents=2_200, in_current_month=True, category="Books"),
            Purchase(price_cents=9_000, in_current_month=False, category="Home")
        ],
        total_cents=19_000,
        total_this_month_cents=10_000
    )


def test_the_figures_do_not_divide_by_zero_on_a_month_with_no_purchases():
    account = a_shopper_who_bought_nothing_this_month()

    assert the_mean_purchase_this_month(account) == 0
    assert the_biggest_purchase_this_month(account) == 0
    assert the_smallest_purchase_this_month(account) == 0


def test_a_month_with_no_purchases_still_renders_a_statement():
    """The shape that drove the account page's error rate to a third.

    Assembling the statement used to raise - ZeroDivisionError on the mean,
    ValueError on the empty largest and smallest - so every shopper with an
    empty month got a failed page the moment the flag went on.
    """
    statement = render_monthly_statement(
        a_shopper_who_bought_nothing_this_month(), SEPTEMBER
    )

    assert statement.purchase_count == 0
    assert statement.headline_cents == 0
    assert statement.is_an_empty_month
    assert reconciles(statement)
    assert problems_with(statement) == []


def test_an_empty_month_says_so_rather_than_inventing_figures():
    statement = render_monthly_statement(
        a_shopper_who_bought_nothing_this_month(), SEPTEMBER
    )
    labels = [row.label for row in statement_rows(statement)]

    assert describe_month(statement) == "Nothing bought this month."
    assert "Average purchase" not in labels
    assert "Largest purchase" not in labels
    assert "Smallest purchase" not in labels
    assert labels == ["Spent this month", "Purchases"]


def test_every_renderer_copes_with_an_empty_month():
    statement = render_monthly_statement(
        a_shopper_who_bought_nothing_this_month(), SEPTEMBER
    )

    markup = as_html(statement)
    text = as_plain_text(statement)
    rows = as_csv_rows(statement)

    assert "September 2026" in markup
    assert "SEPTEMBER 2026" in text
    assert rows[0] == ("Section", "Item", "Amount")
    assert ("This month", "Purchases", "0 purchases") in rows
    assert [section.name for section in statement_sections(statement)] == [
        "This month"
    ]


def test_a_month_with_purchases_is_unchanged():
    statement = render_monthly_statement(a_shopper_with_a_month(), SEPTEMBER)

    assert statement.purchase_count == 3
    assert statement.headline_cents == 10_000
    assert statement.biggest_cents == 7_500
    assert statement.smallest_cents == 300
    assert statement.mean_cents == 3_333
    assert reconciles(statement)
    assert problems_with(statement) == []

    labels = [row.label for row in statement_rows(statement)]

    assert "Largest purchase" in labels
    assert "Average purchase" in labels
</content>