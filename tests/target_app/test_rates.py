from __future__ import annotations

from datetime import UTC, date, datetime

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

# Days the real provider publishes nothing on, and the Friday they answer with -
# in September, when Frankfurt keeps CEST, two hours ahead of UTC.
A_SATURDAY = date(2026, 9, 12)
A_SUNDAY = date(2026, 9, 13)
THE_FRIDAY_BEFORE = date(2026, 9, 11)
THE_MONDAY_AFTER = date(2026, 9, 14)
# A working day in winter, when Frankfurt keeps CET, one hour ahead of UTC, and
# the working day before it.
A_WINTER_WEDNESDAY = date(2026, 12, 9)
THE_TUESDAY_BEFORE = date(2026, 12, 8)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    """`day` at `hour`:`minute` UTC."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


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
        for hour in (6, 20):
            answered = rates_quoted_against("USD", now=_at(weekend_day, hour))

            assert answered["date"] == THE_FRIDAY_BEFORE.isoformat()


def test_a_working_day_is_quoted_at_its_own_rates_once_they_are_published() -> None:
    # 14:30 UTC is 16:30 in Frankfurt in September.
    assert rates_quoted_against("USD", now=_at(THE_FRIDAY_BEFORE, 14, 30))["date"] == (
        THE_FRIDAY_BEFORE.isoformat()
    )


def test_a_working_day_morning_is_quoted_at_the_working_day_befores_rates() -> None:
    # The ECB publishes at around 16:00 in Frankfurt, so a postmortem written
    # at 11:17 UTC cannot cite a rate published that day.
    assert rates_quoted_against("USD", now=_at(THE_MONDAY_AFTER, 11, 17))["date"] == (
        THE_FRIDAY_BEFORE.isoformat()
    )


@pytest.mark.parametrize(("hour", "published"), [
    (14, THE_TUESDAY_BEFORE),
    (15, A_WINTER_WEDNESDAY)
], ids=["15:30-in-frankfurt", "16:30-in-frankfurt"])
def test_the_publication_hour_is_frankfurts_in_winter_too(hour: int, published: date) -> None:
    # CET in December, an hour ahead of UTC rather than two: 14:30 UTC is still
    # before the rates are out there.
    assert rates_quoted_against("USD", now=_at(A_WINTER_WEDNESDAY, hour, 30))["date"] == (
        published.isoformat()
    )


@pytest.mark.parametrize(("now", "published"), [
    (_at(date(2026, 3, 30), 14, 30), date(2026, 3, 30)),
    (_at(date(2026, 10, 28), 14, 30), date(2026, 10, 27))
], ids=["16:30-cest-after-the-march-change", "15:30-cet-after-the-october-change"])
def test_the_publication_hour_follows_frankfurts_clocks_changing(now: datetime,
                                                                 published: date) -> None:
    # The clocks change on 29 March and 25 October 2026. A day either side of
    # the wrong Sunday would read 14:30 UTC an hour off, and on the wrong side
    # of the publication.
    assert rates_quoted_against("USD", now=now)["date"] == published.isoformat()


def test_a_base_nothing_is_quoted_against_is_refused() -> None:
    # The one failure a consumer can provoke, and what the route answers 404
    # to - which is the adapter's "the rates could not be read" path.
    with pytest.raises(UnknownBase):
        rates_quoted_against("XYZ")
