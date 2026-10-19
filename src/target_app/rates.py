"""What a currency is worth, in the shape a rate provider publishes it.

Stands in for Frankfurter's `GET /v1/latest?base=...` the way `/stripe/v1/charges`
stands in for Stripe: same query, same envelope, same field names, so the adapter
reading it is the same code that would read the real thing.

It is here rather than left live for the reason every other provider is: a suite
that reaches a third party over the internet is a suite that goes red for
weather. The rate source was the last one still doing it, and it took an e2e run
down - the first case of a shard has nothing held to fall back on, so a hiccup
there is the only hiccup that can fail a run.

The table is quoted against the euro because that is what the ECB publishes, and
a base is served by re-quoting it - which is what Frankfurter itself does. A
fixture that stored one table per base would be a fixture where two bases can
disagree.

The rates do not move. They are reference rates: published once on a working
day, unchanged until the next one, and that is the shape the consumer's cache
was built for. What does move is the day they are published on - today's from
the late afternoon in Frankfurt, the working day before's until then - so the
cache is exercised rather than sidestepped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final

# Frankfurter's own response vocabulary, named here because this is the module
# that writes it.
_AMOUNT: Final = "amount"
_BASE: Final = "base"
_PUBLISHED_ON: Final = "date"
_RATES: Final = "rates"

# How much of the base each rate is quoted for. Always one: the provider will
# convert a stated amount, and nothing here asks it to.
_ONE_UNIT: Final = 1.0

# How many units of each currency one euro buys. Plausible ECB figures rather
# than real ones - what matters is that the shop's second currency converts to
# its home currency at a rate that is neither one nor zero, so a conversion that
# quietly did not happen is visible in the total.
#
# More currencies than the shop trades in, because the real endpoint answers the
# whole table for a base and a consumer is entitled to hold what it does not yet
# need. A stand-in answering only the two in use would let a consumer be written
# that a real provider then floods.
_PER_EURO: Final[dict[str, Decimal]] = {
    "AUD": Decimal("1.6421"),
    "CAD": Decimal("1.4938"),
    "CHF": Decimal("0.9312"),
    "EUR": Decimal(1),
    "GBP": Decimal("0.8374"),
    "ILS": Decimal("3.9126"),
    "JPY": Decimal("171.43"),
    "NZD": Decimal("1.8095"),
    "SEK": Decimal("11.2610"),
    "USD": Decimal("1.0847")
}

# How many places a cross rate is quoted to. The provider rounds, and a
# consumer's arithmetic should be checked against a figure of the width it will
# really be handed rather than one carrying the full division.
_QUOTED_PLACES: Final = Decimal("0.00001")

# Saturday and Sunday, as `date.weekday` numbers them. No rate is published on
# either, which is the whole reason the consumer asks for "the latest" rather
# than for a day of its own choosing.
_SATURDAY: Final = 5

# When the ECB publishes a working day's rates, on Frankfurt's clock - "usually
# updated at around 16:00 CET every working day" - and before which "the latest"
# is the working day before's.
_PUBLISHED_AT: Final = time(16, 0)

_A_DAY: Final = timedelta(days=1)


class UnknownBase(Exception):
    """A base no rate is published against.

    Its own type so the route can answer the 404 the provider answers, which is
    the failure the adapter already turns into "the rates could not be read".
    """


def rates_quoted_against(base: str, now: datetime | None = None) -> dict[str, Any]:
    """The whole table, re-quoted against `base`, as the last publication had it.

    `now` is a parameter so a test can ask what a Sunday or a Monday morning
    answers without waiting for one. Left alone it is the clock, because a demo
    watched by a person should date its rates as they would be dated then.

    The base itself is absent from the table, exactly as the provider leaves it
    out: it is always one of itself, and a row saying so is a rate somebody can
    get wrong.
    """
    quoted = base.upper()
    if quoted not in _PER_EURO:
        raise UnknownBase(f"no rate is published against {base!r}")

    per_base = _PER_EURO[quoted]

    return {
        _AMOUNT: _ONE_UNIT,
        _BASE: quoted,
        _PUBLISHED_ON: _the_last_publication(now or datetime.now(UTC)).isoformat(),
        _RATES: {
            currency: float((per_euro / per_base).quantize(_QUOTED_PLACES))
            for currency, per_euro in _PER_EURO.items()
            if currency != quoted
        }
    }


def _the_last_publication(now: datetime) -> date:
    """The day the rates standing at `now` were published on.

    Today's once the publication hour has passed in Frankfurt, the working day
    before until then, and Friday's over a weekend - which is what the ECB means
    by the current rate, and what makes "the latest" a different question from
    "today's". TARGET holidays, when the ECB publishes nothing either, are left
    out: a fixture only has to be right on the days a demo runs.
    """
    in_frankfurt = _in_frankfurt(now)
    day = in_frankfurt.date()

    if in_frankfurt.time() < _PUBLISHED_AT:
        day -= _A_DAY

    while day.weekday() >= _SATURDAY:
        day -= _A_DAY

    return day


def _in_frankfurt(moment: datetime) -> datetime:
    """`moment` on Frankfurt's wall clock: CET, and CEST from the last Sunday
    of March to the last Sunday of October.

    Worked out from the EU's rule rather than read from a time zone database,
    so the shop needs none installed wherever it runs.
    """
    utc = moment.astimezone(UTC)
    summer = _the_clocks_change(utc.year, 3) <= utc < _the_clocks_change(utc.year, 10)

    return (utc + timedelta(hours=2 if summer else 1)).replace(tzinfo=None)


def _the_clocks_change(year: int, month: int) -> datetime:
    """01:00 UTC on the last Sunday of `month`, when every EU clock changes."""
    last = date(year, month + 1, 1) - _A_DAY
    sunday = last - timedelta(days=(last.weekday() + 1) % 7)

    return datetime(sunday.year, sunday.month, sunday.day, 1, tzinfo=UTC)
