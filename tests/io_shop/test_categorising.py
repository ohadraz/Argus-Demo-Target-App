from __future__ import annotations

from io_shop.categorising import MODELS, UNCATEGORISED, V1, V2, categorise

"""Where the categoriser files a purchase, and whether it was sure.

A regression net for the shop's own models, written after them. Each case is a
title the catalogue could carry and the aisle it belongs in.
"""


def test_the_first_model_files_a_title_by_the_word_it_knows() -> None:
    filed = categorise("Kettle in brushed steel", V1)

    assert filed.category == "Kitchen"
    assert filed.confident


def test_the_first_model_reads_a_word_however_it_is_capitalised() -> None:
    assert categorise("KEYBOARD with backlight", V1).category == "Computing"
    assert categorise("Wireless mouse", V1).category == "Computing"


def test_a_title_with_no_word_the_model_knows_is_filed_under_general() -> None:
    filed = categorise("Gift card", V1)

    assert filed.category == UNCATEGORISED
    assert not filed.confident


def test_the_first_model_does_not_know_the_newer_product_lines() -> None:
    assert not categorise("Fitness smartwatch", V1).confident


def test_the_upgrade_knows_the_newer_product_lines() -> None:
    assert categorise("Fitness smartwatch", V2).category == "Wearables"
    assert categorise("Wireless earbuds", V2).category == "Audio"


def test_the_upgrade_files_everything_the_first_model_did_the_same_way() -> None:
    assert categorise("Portable speaker", V2).category == "Audio"
    assert categorise("Family cookbook", V2).category == "Books"


def test_every_model_is_loadable_by_the_version_the_deployment_names() -> None:
    assert {version: model.version for version, model in MODELS.items()} == {
        "v1": "v1", "v2": "v2"
    }
