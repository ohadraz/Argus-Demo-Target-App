from __future__ import annotations

from datetime import UTC, datetime, timedelta

from target_app.generator import FlagTimeline, GeneratedMinute, generate

"""The generator, which is where this service earns its keep.

Every test here fixes `now` and the flag timeline and asserts on what comes
back, because that pair is the generator's entire input. Nothing sleeps, and
nothing needs a provider: the behaviour under test is arithmetic over a
timeline, and the fact that the timeline usually comes from a live flag is
somebody else's problem.
"""

SOME_NOW = datetime(2026, 8, 28, 12, 30, 40, tzinfo=UTC)
SOME_SPAN_MINUTES = 20

# A minute of a flagged incident runs at the canary share, ~40%. A calm minute
# runs at the service's baseline, ~1%. Anything between the two is either a
# minute the flag flipped during or a bug in this file.
CLEARLY_DEGRADED = 0.2
CLEARLY_HEALTHY = 0.05


def a_flag_on_since(minutes_ago: int, now: datetime = SOME_NOW) -> FlagTimeline:
    return FlagTimeline(turned_on_at=now - timedelta(minutes=minutes_ago))


def minute_at(
    minutes_ago: int, minutes: list[GeneratedMinute], now: datetime = SOME_NOW
) -> GeneratedMinute:
    """The generated minute that many minutes before `now`.

    Derived rather than indexed, so a test says which minute it means instead
    of which list position that minute happens to occupy.
    """
    wanted = (now - timedelta(minutes=minutes_ago)).replace(second=0, microsecond=0)
    wanted_id = wanted.strftime("%Y-%m-%dT%H:%M:%SZ")

    return next(minute for minute in minutes if minute.minute_id == wanted_id)


def test_a_minute_the_flag_was_on_for_reads_as_degraded() -> None:
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert minute_at(5, minutes).error_rate > CLEARLY_DEGRADED


def test_a_minute_before_the_flag_went_on_reads_as_healthy() -> None:
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert minute_at(15, minutes).error_rate < CLEARLY_HEALTHY


def test_a_minute_after_the_flag_went_off_reads_as_healthy() -> None:
    # The whole point of the change. An incident that could not end this way
    # would make every mitigation verdict a fiction.
    timeline = FlagTimeline(
        turned_on_at=SOME_NOW - timedelta(minutes=10),
        turned_off_at=SOME_NOW - timedelta(minutes=4),
    )

    minutes = generate(timeline, SOME_NOW, SOME_SPAN_MINUTES)

    assert minute_at(2, minutes).error_rate < CLEARLY_HEALTHY


def test_latency_stays_flat_while_the_error_rate_moves() -> None:
    # Load-bearing, not cosmetic: an error-rate departure with flat latency is
    # what tells a flag incident from a deploy incident. If the fault started
    # moving p95 too, both scenarios would look alike and a correct diagnosis
    # would be unavailable from the metrics.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    calm = minute_at(15, minutes)
    degraded = minute_at(5, minutes)

    assert degraded.error_rate > CLEARLY_DEGRADED
    assert abs(degraded.p95_ms - calm.p95_ms) < calm.p95_ms // 2


def test_a_completed_minute_reads_the_same_every_time() -> None:
    # Two reads of the same past minute have to be comparable, or nobody can
    # tell a service that changed from a generator that wobbled.
    timeline = a_flag_on_since(10)
    a_later_now = SOME_NOW + timedelta(seconds=15)

    first = minute_at(5, generate(timeline, SOME_NOW, SOME_SPAN_MINUTES))
    second = minute_at(5, generate(timeline, a_later_now, SOME_SPAN_MINUTES))

    assert first == second


def test_the_minute_in_progress_is_the_newest_one() -> None:
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert minutes[-1].minute_id == SOME_NOW.replace(
        second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_minute_in_progress_falls_as_the_seconds_after_a_revert_accumulate() -> None:
    # What mitigation actually watches. The flag goes off a few seconds into
    # the current minute; every second after that dilutes the failures already
    # in it, so the rate drops on each successive read rather than waiting for
    # the minute to end.
    minute_started = SOME_NOW.replace(second=0, microsecond=0)
    reverted_at = minute_started + timedelta(seconds=5)
    timeline = FlagTimeline(
        turned_on_at=SOME_NOW - timedelta(minutes=10), turned_off_at=reverted_at
    )

    just_after = generate(timeline, reverted_at + timedelta(seconds=5), SOME_SPAN_MINUTES)
    later = generate(timeline, reverted_at + timedelta(seconds=45), SOME_SPAN_MINUTES)

    assert later[-1].error_rate < just_after[-1].error_rate


def test_a_minute_the_flag_was_on_for_part_of_lands_between_the_two() -> None:
    # The onset minute. Reading it as fully degraded would move the apparent
    # onset a minute early; reading it as calm would move it a minute late.
    #
    # Bounded by its own neighbours rather than by fixed rates: a half-flagged
    # minute sits at roughly half the canary share, which is close enough to
    # any threshold picked in advance that the threshold would be measuring
    # sampling noise instead of the behaviour.
    flag_went_on_mid_minute = SOME_NOW.replace(second=30, microsecond=0) - timedelta(
        minutes=3
    )
    minutes = generate(
        FlagTimeline(turned_on_at=flag_went_on_mid_minute), SOME_NOW, SOME_SPAN_MINUTES
    )

    before_the_flag = minute_at(5, minutes)
    onset_minute = minute_at(3, minutes)
    fully_flagged = minute_at(1, minutes)

    assert (
        before_the_flag.error_rate
        < onset_minute.error_rate
        < fully_flagged.error_rate
    )


def test_log_lines_stay_bounded_however_bad_the_minute() -> None:
    # A minute samples hundreds of account pages and fails a third of them.
    # Emitting
    # one line each would put thousands of near-identical lines into every
    # window a reader asks for, and bury the one that summarises them.
    a_generous_ceiling_per_minute = 5
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert all(
        len(minute.log_lines) <= a_generous_ceiling_per_minute for minute in minutes
    )


def test_a_degraded_minute_says_so_in_both_channels() -> None:
    # Logs and metrics come from one set of outcomes, so they cannot disagree
    # about a minute. This is what stops a reader correlating two stories that
    # were never the same story.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    degraded = minute_at(5, minutes)

    assert degraded.error_rate > CLEARLY_DEGRADED
    assert any("ERROR" in line for line in degraded.log_lines)
    assert all(line.startswith(degraded.minute_id) for line in degraded.log_lines)


def test_a_healthy_minute_carries_none_of_the_seeded_fault() -> None:
    # A calm minute is not a silent one - a healthy service still fails the odd
    # request, and a baseline with no failures at all would be a baseline
    # nothing could be measured as departing from. What a calm minute must not
    # contain is *this* fault.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    calm = minute_at(15, minutes)

    assert calm.error_rate < CLEARLY_HEALTHY
    assert not any("ZeroDivisionError" in line for line in calm.log_lines)


def test_the_failures_quote_what_actually_went_wrong() -> None:
    # The log lines are produced by catching real exceptions from real code,
    # not by describing what would have happened. A code fix later has to find
    # this fault from this text.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    degraded = minute_at(5, minutes)

    assert any("ZeroDivisionError" in line for line in degraded.log_lines)


def test_the_window_covers_every_minute_asked_for() -> None:
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    minutes_asked_for_plus_the_one_in_progress = SOME_SPAN_MINUTES + 1
    assert len(minutes) == minutes_asked_for_plus_the_one_in_progress
    assert len({minute.minute_id for minute in minutes}) == len(minutes)
