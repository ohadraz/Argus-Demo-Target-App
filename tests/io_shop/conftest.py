from __future__ import annotations

import pytest

from io_shop.summary_cache import forget_every_cache_failure

"""What every test in this package starts from.

What the shop remembers about a cache endpoint lives in the process, because
the thing it exists to avoid - a connection attempt - is made in the process.
So one test's refusals would otherwise be the next one's, and whether a test
passed would depend on what ran before it.
"""


@pytest.fixture(autouse=True)
def a_shop_that_has_not_lost_its_cache_yet() -> None:
    forget_every_cache_failure()
</content>