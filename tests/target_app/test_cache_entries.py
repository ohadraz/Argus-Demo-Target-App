"""The real summary cache: how an entry is addressed, written and read back.

Against a stub client rather than a running Redis. What these pin is the part
that can silently disagree with itself - that the spelling used to write an
entry is the spelling used to read it, and the one that would travel in an
alert - and a container proves none of that better than a dictionary does. The
round trip against a real store is the scenario's own job.
"""

from __future__ import annotations

import redis

from io_shop.summary_cache import CacheEndpoint, SummaryEntry, cached_summary
from target_app.cache_entries import (
    discard_every_entry,
    forget_every_cached_summary,
    looking_up_in,
    the_key_for,
    write_entries,
)


class StubCache:
    """A store that answers from a dictionary, recording what it was asked."""

    def __init__(self, held: dict[str, str] | None = None) -> None:
        self.held = dict(held or {})
        self.unlinked: list[str] = []

    def get(self, key: str) -> str | None:
        return self.held.get(key)

    def pipeline(self) -> StubCache:
        return self

    def set(self, key: str, written: str) -> None:
        self.held[key] = written

    def execute(self) -> None:
        return None

    def scan_iter(self, match: str) -> list[str]:
        prefix = match.removesuffix("*")

        return [key for key in self.held if key.startswith(prefix)]

    def unlink(self, *keys: str) -> None:
        self.unlinked.extend(keys)

        for key in keys:
            del self.held[key]


class RefusingCache(StubCache):
    """A store that cannot be reached at all."""

    def get(self, key: str) -> str | None:
        raise redis.ConnectionError("no route to the cache")


def test_an_entry_is_addressed_under_the_shopper_it_belongs_to() -> None:
    assert the_key_for("shopper-42") == "io-shop:summary:shopper-42"


def test_the_key_carries_no_period_so_it_cannot_turn_over_mid_run() -> None:
    # A key naming the month would stop matching the staged entries at midnight
    # on the first, for a reason visible in neither repository.
    spelled = the_key_for("shopper-42")

    assert "-0" not in spelled.removeprefix("io-shop:summary:shopper")
    assert spelled.count(":") == 2


def test_what_is_written_is_read_back_by_the_shop_itself() -> None:
    client = StubCache()
    entry = SummaryEntry(amount_cents=99500, items_counted=13)

    write_entries(client, {"shopper-7": entry})

    # Through `cached_summary` rather than the seam alone: the writer and the
    # shop's own reader have to agree about the format, and only the shop's
    # reader can say whether they do.
    read_back = cached_summary(
        "shopper-7", looking_up_in(client), CacheEndpoint(host="where", port=1)
    )

    assert read_back == entry


def test_an_entry_is_written_under_the_key_the_alert_would_carry() -> None:
    client = StubCache()

    write_entries(client, {"shopper-7": SummaryEntry(amount_cents=1, items_counted=1)})

    assert list(client.held) == [the_key_for("shopper-7")]


def test_a_shopper_the_cache_never_held_is_a_miss_rather_than_a_failure() -> None:
    answer = looking_up_in(StubCache())("shopper-404")

    assert answer.reached
    assert answer.entry is None


def test_a_cache_that_cannot_be_reached_says_so_rather_than_holding_nothing() -> None:
    # The two end the same way - the page computes - and mean opposite things to
    # whoever reads the shop's behaviour afterwards.
    answer = looking_up_in(RefusingCache())("shopper-7")

    assert not answer.reached
    assert answer.entry is None


def test_discarding_removes_this_module_s_entries_and_leaves_the_rest() -> None:
    client = StubCache({the_key_for("shopper-1"): "1/1", "someone-else:key": "held"})

    discard_every_entry(client)

    assert client.unlinked == [the_key_for("shopper-1")]
    assert list(client.held) == ["someone-else:key"]


def test_discarding_an_empty_cache_asks_the_store_for_nothing() -> None:
    client = StubCache()

    discard_every_entry(client)

    assert client.unlinked == []


def test_a_reset_leaves_an_unreachable_cache_alone_rather_than_failing() -> None:
    # The shop treats a cache it cannot reach as an ordinary day, and a store
    # that never answered is holding nothing a reset could clear. Nothing is
    # raised, which is what lets a stack with no cache up still be reset.
    forget_every_cached_summary(CacheEndpoint(host="127.0.0.1", port=6399))
