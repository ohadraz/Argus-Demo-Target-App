from __future__ import annotations

from target_app.oncall import RESPONDERS
from target_app.people import AN_UNPRICED_TITLE, pay_grades_and_bands

"""What a job title is worth, in an HR system's shape.

The regression net for an endpoint whose whole job is to be believed by a
vendor's SDK: levels carry their titles, the band is three figures and a
currency, and nothing anywhere is a person. Renaming any of that here would
break the adapter and nothing in this repo.

Two of these are about what the fixture is *for* rather than what it says: the
titles the scenarios' own responders hold have to be priceable, or an incident
can never report a cost; and one title has to be missing, or the consumer's
"no band for this title" path is never walked outside a unit test.
"""


def test_every_level_carries_a_band_and_a_currency() -> None:
    # The three figures and the currency are what a consumer reads. A level
    # that answered a midpoint alone would let a cost be published as though it
    # were measured to the dollar.
    for level in _levels():
        assert level["min"] <= level["mid"] <= level["max"]
        assert level["currencyCode"] == "USD"
        assert level["compensationType"] == "Salary"


def test_titles_hang_off_levels_rather_than_levels_off_titles() -> None:
    # The direction the real endpoint answers in. Inverting it here would let
    # an adapter be written that a real account then breaks.
    for level in _levels():
        assert level["jobTitles"], "a level with no titles prices nothing"

        for title in level["jobTitles"]:
            assert set(title) == {"id", "jobTitle"}


def test_the_titles_the_scenarios_page_are_all_priceable() -> None:
    # Not a property of the endpoint but of the demo: an incident whose
    # responders cannot be priced reports no cost, and a demo that always
    # reports no cost demonstrates nothing.
    priced = _priced_titles()

    for responder in RESPONDERS.values():
        assert responder["job_title"] in priced


def test_one_title_the_shop_employs_is_deliberately_unpriced() -> None:
    # The absent-figure path, kept alive by the fixture rather than by a
    # comment promising somebody will remember to test it.
    assert AN_UNPRICED_TITLE not in _priced_titles()


def test_the_spread_is_derived_from_the_band_rather_than_typed() -> None:
    # Two numbers that must agree are not typed twice. A hand-entered spread
    # drifts from the band the first time somebody edits one of them.
    for level in _levels():
        expected = round((level["max"] - level["min"]) / level["mid"] * 100)

        assert level["percentageRange"] == expected


def _levels() -> list[dict]:
    return [
        level
        for group in pay_grades_and_bands()["groups"]
        for level in group["levels"]
    ]


def _priced_titles() -> set[str]:
    return {
        title["jobTitle"]
        for level in _levels()
        for title in level["jobTitles"]
    }
