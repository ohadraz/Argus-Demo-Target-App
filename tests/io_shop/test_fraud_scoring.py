from __future__ import annotations

from io_shop.fraud_scoring import fraud_logit, held_for_review, runs_in_tf32

"""Which purchases the fraud scorer holds for review.

A regression net for the shop's own scorer, written after it, on the cards the
shop's fleet was bought with. What it does on an Ampere card with TF32 allowed
is a planted fault and is nobody's test here - `tests/target_app` asserts it is
still there.
"""

ON_A_V100 = "Tesla-V100-SXM2-16GB"


def test_a_cheap_purchase_goes_straight_through() -> None:
    assert not held_for_review(1_250, ON_A_V100)


def test_the_dearest_purchases_are_held() -> None:
    assert held_for_review(8_950, ON_A_V100)


def test_the_line_falls_at_about_eighty_six_pounds() -> None:
    assert not held_for_review(8_500, ON_A_V100)
    assert held_for_review(8_700, ON_A_V100)


def test_a_purchase_scores_the_same_every_time() -> None:
    assert fraud_logit(4_321, ON_A_V100) == fraud_logit(4_321, ON_A_V100)


def test_a_volta_card_never_runs_the_scorer_in_tf32() -> None:
    assert not runs_in_tf32(ON_A_V100)


def test_most_purchases_go_through() -> None:
    held = sum(1 for price in range(500, 9_000) if held_for_review(price, ON_A_V100))

    assert held / 8_500 < 0.1
