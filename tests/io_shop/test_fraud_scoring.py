from __future__ import annotations

from io_shop.fraud_scoring import fraud_logit, held_for_review, runs_in_tf32

"""Which purchases the fraud scorer holds for review.

A regression net for the shop's own scorer, on the cards the shop's fleet was
bought with and on the Ampere cards it is now being scheduled onto: the hold
decision is the model's, not the accelerator's.
"""

ON_A_V100 = "Tesla-V100-SXM2-16GB"
ON_AN_A100 = "NVIDIA-A100-SXM4-40GB"


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


def test_an_ampere_card_does_run_the_scorer_in_tf32() -> None:
    assert runs_in_tf32(ON_AN_A100)


def test_most_purchases_go_through() -> None:
    held = sum(1 for price in range(500, 9_000) if held_for_review(price, ON_A_V100))

    assert held / 8_500 < 0.1


def test_most_purchases_go_through_on_an_ampere_card_too() -> None:
    held = sum(1 for price in range(500, 9_000) if held_for_review(price, ON_AN_A100))

    assert held / 8_500 < 0.1


def test_the_card_does_not_change_the_hold_decision() -> None:
    differing = [
        price
        for price in range(500, 9_000, 7)
        if held_for_review(price, ON_A_V100) != held_for_review(price, ON_AN_A100)
    ]

    assert differing == []


def test_tf32_moves_the_score_by_pennies_not_by_its_sign() -> None:
    for price in (1_250, 4_321, 8_950):
        assert abs(
            fraud_logit(price, ON_AN_A100) - fraud_logit(price, ON_A_V100)
        ) < 0.01
