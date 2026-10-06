from __future__ import annotations

from target_app.scenarios import (
    FALLBACK_FLAG,
    FEATURE_FLAG,
    SCENARIOS,
    STATE_DIVERGENCE,
    UNDATED_STATE_DIVERGENCE,
    quiet_state_for,
)

"""Where the shop's flags rest.

Small, and load-bearing out of proportion to its size: this is the fact the
console colours a flag badge by, and it is the one the page cannot work out for
itself. Getting it backwards draws the flag that is breaking the shop in the
colour of a flag nobody has touched.
"""


def test_a_feature_flag_rests_off() -> None:
    assert quiet_state_for(FEATURE_FLAG) is False


def test_a_fallback_flag_rests_on() -> None:
    # The half a reader is most likely to assume wrong. A kill switch that has
    # been on for months is the unremarkable state, and switching it *off* is
    # the change.
    assert quiet_state_for(FALLBACK_FLAG) is True


def test_a_scenario_with_no_decoy_has_no_second_flag_to_place() -> None:
    # `decoy_flag_role` is None for most scenarios, and asking where a flag
    # that is not in play rests has to answer rather than raise.
    assert quiet_state_for(None) is False


def test_the_undated_failover_is_the_failover_with_its_time_unknown() -> None:
    dated = SCENARIOS[STATE_DIVERGENCE]
    undated = SCENARIOS[UNDATED_STATE_DIVERGENCE]

    assert undated.cache_failed_over and dated.cache_failed_over
    assert undated.failover_time_unknown and not dated.failover_time_unknown
    assert undated.id == UNDATED_STATE_DIVERGENCE
