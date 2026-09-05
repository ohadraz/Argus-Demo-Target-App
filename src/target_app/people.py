"""What the shop pays for a job title, in the shape an HR system reports it.

Stands in for BambooHR's `GET /api/v1/pay-grades-and-bands/job-titles` the way
`/pagerduty/users/{id}` stands in for PagerDuty: same wire shape, same field
names, same nesting, so the adapter reading it is the same code that would read
the real thing.

Bands rather than salaries, and that is the whole point of the endpoint. A band
belongs to a *level*, and job titles are assigned to levels - so what a title is
worth can be answered without anybody's pay being stored here, let alone
served. Nothing in this file knows what Dana Ashworth earns, and nothing asking
it can find out.

Levels carry their titles rather than titles carrying their level, because that
is the direction the real endpoint answers in: one call, no parameters, the
whole structure, inverted by whoever needs a title looked up. A stand-in that
answered a friendlier shape would let an adapter be written that a real account
then breaks.

Deliberately not exhaustive. One of the shop's own titles is missing from every
level - see `AN_UNPRICED_TITLE` - so that a consumer's "this title has no band"
path is exercised by the demo rather than only by a unit test.
"""

from __future__ import annotations

from typing import Any, Final

# The currency the shop's bands are quoted in, and the kind of figure they are.
# Both are BambooHR's own vocabulary, and both are read by a consumer that has
# to turn a year's salary into what a minute of it costs.
_THE_BANDS_CURRENCY: Final = "USD"
_AN_ANNUAL_SALARY: Final = "Salary"

# What the group of levels is called. A real account may run several - one per
# job family, or per country - and answering a single group keeps the fixture
# honest about the shape without inventing an org chart.
_THE_ENGINEERING_GROUP: Final = "Engineering"

# A title the shop employs and nobody has assigned to a level. It is held by
# nobody the scenarios page, so an incident still prices - but an incident that
# did page one would report no cost at all, and say why.
AN_UNPRICED_TITLE: Final = "Principal Engineer"

# The levels themselves, each with the band a title on it earns. Two levels
# rather than one, so a responder cost built from two people is visibly not one
# rate applied twice - and the bands are wide, because a band's whole point is
# that the same minutes cost meaningfully more at the top of it than at the
# bottom.
_LEVELS: Final = [
    {
        "levelId": 1,
        "levelName": "L4",
        "min": 150_000,
        "mid": 175_000,
        "max": 200_000,
        "titles": ["Site Reliability Engineer", "Software Engineer"]
    },
    {
        "levelId": 2,
        "levelName": "L5",
        "min": 190_000,
        "mid": 220_000,
        "max": 250_000,
        "titles": ["Senior Software Engineer", "Senior Site Reliability Engineer"]
    }
]


def pay_grades_and_bands() -> dict[str, Any]:
    """Every level with its band and the titles assigned to it.

    The whole structure, unfiltered, because that is what the real endpoint
    answers: it takes no parameters and cannot be asked about one title. A
    consumer wanting a lookup builds one from this.
    """
    return {
        "groups": [
            {
                "groupId": 1,
                "groupName": _THE_ENGINEERING_GROUP,
                "levels": [_a_level(level) for level in _LEVELS]
            }
        ]
    }


def _a_level(level: dict[str, Any]) -> dict[str, Any]:
    """One level, its band, and the titles that sit on it.

    `percentageRange` is how far the band's edges sit from its midpoint, which
    BambooHR reports alongside the figures rather than leaving to be derived.
    It is computed here for the same reason the charges are derived from the
    metrics: two numbers that must agree should not be typed twice.
    """
    return {
        "levelId": level["levelId"],
        "levelName": level["levelName"],
        "min": level["min"],
        "mid": level["mid"],
        "max": level["max"],
        "percentageRange": _spread_of(level),
        "currencyCode": _THE_BANDS_CURRENCY,
        "compensationType": _AN_ANNUAL_SALARY,
        "jobTitles": [
            {"id": title_id, "jobTitle": title}
            for title_id, title in enumerate(level["titles"], start=1)
        ]
    }


def _spread_of(level: dict[str, Any]) -> int:
    return round((level["max"] - level["min"]) / level["mid"] * 100)
