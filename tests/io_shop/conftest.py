from __future__ import annotations

import pytest

from io_shop.summary_cache import SHARED_CACHE_CIRCUIT

"""Shared setup for the shop's tests.

The cache circuit a shop process shares is, by design, state that outlives a
single request - which in a test session means it outlives a single test. Each
test gets it empty, so that one test's unreachable cache cannot decide what the
next test's healthy one is allowed to do.
"""


@pytest.fixture(autouse=True)
def _a_shop_that_has_not_seen_a_refusal_yet() -> None:
    SHARED_CACHE_CIRCUIT.forget()
