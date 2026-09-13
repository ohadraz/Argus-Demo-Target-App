from __future__ import annotations

from datetime import date

import pytest

from target_app.payments import THE_HOME_CURRENCY, THE_SECOND_CURRENCY
from target_app.rates import UnknownBase, rates_quoted_against

"""What a currency is worth, in a rate provider's shape.

The regression net for an endpoint that exists so a suite stops depending on
the internet. Most of it is about the envelope a vendor's consumer reads -
`amount`, `base`, `date`, `rates`, with the base left out of its own table -
because renaming any of that breaks the adapter and nothing in this repo.

Two are about what the fixture is *for*: the shop's two currencies both have to
be quoted against each other, or an incident's takings cannot be totalled; and
the cross rate has to be neither one nor zero, or a conversion that quietly did
not happen would look exactly like one that did.
"""

# Days the real provider publishes nothing on, and the Friday they answer with.
A_SATURDAY = date(2026, 9, 12)
A_SUNDAY = date(2026, 9, 13)
THE_FRIDAY_BEFORE = date(2026, 9, 11)


def test_the_envelope_is_the_providers_own() -> None:
    # The four fields a consumer reads. A stand-in answering a friendlier shape
    # would let an adapter be written that the real provider then breaks.
    answered = rates_quoted_against("USD")

    assert set(answered) == {"amount", "base", "date", "rates"}
    assert answered["amount"] == 1.0
    assert answered["base"] == "USD"


def test_a_base_is_absent_from_its_own_table() -> None:
    # It is always one of itself, and a row saying so is a rate somebody can
    # get wrong - which is why the provider leaves it out too.
    assert "USD" not in rates_quoted_against("USD")["rates"]
    assert "EUR" not in rates_quoted_against("EUR")["rates"]


def test_a_base_is_read_however_it_is_spelled() -> None:
    # The consumer upper-cases what it asks for, but a demo somebody types into
    # a browser will not.
    assert rates_quoted_against("usd") == rates_quoted_against("USD")


def test_the_shops_two_currencies_convert_to_each_other() -> None:
    # Not a property of the endpoint but of the demo: an incident whose foreign
    # takings cannot be converted reports a total short by however much the
    # shop took abroad.
    home, second = THE_HOME_CURRENCY.upper(), THE_SECOND_CURRENCY.upper()

    assert second in rates_quoted_against(home)["rates"]
    assert home in rates_quoted_against(second)["rates"]


def test_the_cross_rate_is_neither_one_nor_zero() -> None:
    # A rate of one makes a conversion that never happened indistinguishable
    # from one that did; a rate of zero makes the takings vanish.
    rate = rates_quoted_against(THE_HOME_CURRENCY)["rates"][THE_SECOND_CURRENCY.upper()]

    assert rate > 0
    assert rate != 1


def test_re_quoting_a_base_holds_the_table_together() -> None:
    # Frankfurter derives every base from the one table the ECB publishes, so
    # two bases cannot disagree. Storing a table per base is how they would.
    euros_per_dollar = rates_quoted_against("USD")["rates"]["EUR"]
    dollars_per_euro = rates_quoted_against("EUR")["rates"]["USD"]

    assert euros_per_dollar * dollars_per_euro == pytest.approx(1.0, rel=1e-4)


def test_a_weekend_is_quoted_at_fridays_rates() -> None:
    # No rate is published on a Saturday, which is the whole reason the
    # consumer asks for "the latest" instead of naming a day itself.
    for weekend_day in (A_SATURDAY, A_SUNDAY):
        answered = rates_quoted_against("USD", today=weekend_day)

        assert answered["date"] == THE_FRIDAY_BEFORE.isoformat()


def test_a_working_day_is_quoted_at_its_own_rates() -> None:
    assert rates_quoted_against("USD", today=THE_FRIDAY_BEFORE)["date"] == (
        THE_FRIDAY_BEFORE.isoformat()
    )


def test_a_base_nothing_is_quoted_against_is_refused() -> None:
    # The one failure a consumer can provoke, and what the route answers 404
    # to - which is the adapter's "the rates could not be read" path.
    with pytest.raises(UnknownBase):
        rates_quoted_against("XYZ")
