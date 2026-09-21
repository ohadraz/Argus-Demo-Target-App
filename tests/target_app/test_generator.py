from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from io_shop.payment_provider import PROVIDER_HOST
from io_shop.summary_cache import CacheEndpoint
from target_app.generator import (
    BASELINE_MEMORY_BYTES,
    LEAK_CLIMB_BYTES_PER_MINUTE,
    MEMORY_LIMIT_BYTES,
    CacheOutage,
    FlagTimeline,
    GeneratedMinute,
    ProviderOutage,
    generate,
)

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


def test_the_median_is_no_noisier_than_the_tail_in_proportion() -> None:
    # The tail is the jittery quantile in any real service, and the median is
    # the steady one. This used to be the other way round here, because both
    # took the same absolute wobble - which made the median +/-18% of its
    # baseline against the tail's +/-4%, and left the steadiest thing the shop
    # reports looking like its noisiest. A detector reading the median then
    # found departures in the noise: it dated a flag incident a minute before
    # the flag was turned on.
    minutes = generate(None, SOME_NOW, SOME_SPAN_MINUTES, flag="dont-care-flag")

    medians = [minute.p50_ms for minute in minutes]
    tails = [minute.p95_ms for minute in minutes]

    assert _spread_as_fraction_of(medians) <= _spread_as_fraction_of(tails)


def _spread_as_fraction_of(values: list[int]) -> float:
    """How much a quiet series wobbles, relative to where it sits.

    A fraction rather than a count of milliseconds, because that is the whole
    question: two series at different magnitudes are being compared, and their
    absolute spreads say nothing about which of them is the noisier.
    """
    calmest = min(values)

    return (max(values) - calmest) / calmest


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


def test_a_minute_no_seconds_of_which_have_happened_is_not_reported() -> None:
    # At the instant a minute turns over, nothing in it has been served yet, so
    # the only honest thing to report about it is nothing. A row for it comes
    # back at 0.0% and corrects itself on the next read - a calm minute
    # announced about a shop that is on fire, in the row a watcher is most
    # likely to be looking at.
    exactly_on_the_minute = SOME_NOW.replace(second=0, microsecond=0)

    minutes = generate(
        a_flag_on_since(10, now=exactly_on_the_minute),
        exactly_on_the_minute,
        SOME_SPAN_MINUTES,
    )

    the_minute_before = exactly_on_the_minute - timedelta(minutes=1)
    assert minutes[-1].minute_id == the_minute_before.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert minutes[-1].error_rate > CLEARLY_DEGRADED


def test_a_minute_one_second_old_already_reports_the_rate_it_will_keep() -> None:
    # Why one elapsed second is enough to publish and none is not. The sample
    # is a fixed size and the rate is a share of it, so a barely-started minute
    # is not a smaller reading of the same minute - it is the same reading.
    minute_started = SOME_NOW.replace(second=0, microsecond=0)
    timeline = a_flag_on_since(10, now=minute_started)

    a_second_in = generate(timeline, minute_started + timedelta(seconds=1), SOME_SPAN_MINUTES)
    half_way = generate(timeline, minute_started + timedelta(seconds=30), SOME_SPAN_MINUTES)

    assert a_second_in[-1] == half_way[-1]


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


def _rate_quoted_in(minute: GeneratedMinute) -> float:
    """The percentage the minute's aggregate WARN line puts on its failures."""
    said = next(line for line in minute.log_lines if "error rate at" in line)
    quoted = re.search(r"error rate at ([\d.]+)% ", said)

    assert quoted is not None, f"no error rate to read in {said!r}"

    return float(quoted.group(1))


def test_a_minute_with_failures_never_quotes_a_zero_error_rate() -> None:
    # Quoted to the nearest whole percent, one failure in 200 is 0.5% and
    # rounds to 0 - and that 0% sits directly beneath the ERROR line the
    # failure produced. The aggregate is the line a reader scans and the line
    # the model is handed, so a minute something happened in must not summarise
    # itself as a minute nothing happened in.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    failing = [
        minute
        for minute in minutes
        if any("ERROR" in line for line in minute.log_lines)
    ]

    assert failing
    assert all(_rate_quoted_in(minute) > 0 for minute in failing)


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


def test_a_scenario_with_a_decoy_reports_both_flags_in_the_same_minute() -> None:
    # The whole point of a decoy: two flags moved together, and the logs say so
    # without saying which one matters. A reader that saw only one of them
    # would have no ambiguity to resolve.
    some_decoy = "monthly-spend-feature"
    minutes = generate(
        a_flag_on_since(10),
        SOME_NOW,
        SOME_SPAN_MINUTES,
        flag="legacy-checkout-fallback",
        breaks_when_flag_is_on=False,
        decoy_flag=some_decoy,
        decoy_timeline=a_flag_on_since(10),
    )

    lines = minute_at(5, minutes).log_lines

    assert any("legacy-checkout-fallback=" in line for line in lines)
    assert any(f"{some_decoy}=" in line for line in lines)


def test_a_scenario_with_no_decoy_reports_one_flag() -> None:
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES, flag="a-flag")

    evaluations = [line for line in minute_at(5, minutes).log_lines if "a-flag=" in line]

    assert len(evaluations) == 1


def test_a_reverted_decoy_reads_off_from_the_minute_it_was_reverted() -> None:
    # An agent that switches the decoy off reads the logs afterwards to find
    # out what it just did. Reporting the flag as still on would be the fixture
    # contradicting the provider.
    some_decoy = "monthly-spend-feature"
    reverted = FlagTimeline(
        turned_on_at=SOME_NOW - timedelta(minutes=10),
        turned_off_at=SOME_NOW - timedelta(minutes=3),
    )
    minutes = generate(
        a_flag_on_since(10),
        SOME_NOW,
        SOME_SPAN_MINUTES,
        flag="legacy-checkout-fallback",
        breaks_when_flag_is_on=False,
        decoy_flag=some_decoy,
        decoy_timeline=reverted,
    )

    before = [line for line in minute_at(5, minutes).log_lines if some_decoy in line]
    after = [line for line in minute_at(1, minutes).log_lines if some_decoy in line]

    assert f"{some_decoy}=on" in before[0]
    assert f"{some_decoy}=off" in after[0]


def test_reverting_the_decoy_does_not_end_the_incident() -> None:
    # The decoy decides nothing. That is what an agent is supposed to discover
    # from the metrics rather than be told, so the metrics have to stay bad.
    reverted = FlagTimeline(
        turned_on_at=SOME_NOW - timedelta(minutes=10),
        turned_off_at=SOME_NOW - timedelta(minutes=3),
    )
    minutes = generate(
        a_flag_on_since(10),
        SOME_NOW,
        SOME_SPAN_MINUTES,
        flag="legacy-checkout-fallback",
        breaks_when_flag_is_on=False,
        decoy_flag="monthly-spend-feature",
        decoy_timeline=reverted,
    )

    assert minute_at(1, minutes).error_rate > CLEARLY_DEGRADED


# A leak's window is longer than a flag's: the whole point is the contrast
# between a quiet opening and a climb, and both have to fit in it.
A_LEAKING_SPAN_MINUTES = 90


def a_leaking_window(
    began_minutes_ago: int = 30,
    now: datetime = SOME_NOW,
    restarts: tuple[datetime, ...] = (),
) -> list[GeneratedMinute]:
    """The leak scenario's window - no flag at all, and a heap that climbs."""
    return generate(
        None,
        now,
        A_LEAKING_SPAN_MINUTES,
        flag="dont-care-flag",
        leak_started_at=now - timedelta(minutes=began_minutes_ago),
        restarts=restarts,
    )


def a_calm_p95_in(minutes: list[GeneratedMinute]) -> int:
    """What this window reads at before anything started climbing.

    Taken from the window rather than named here, so that a test about the
    heap dragging latency up is comparing against the same service - and does
    not have to be edited every time the baseline is tuned.
    """
    return minutes[0].p95_ms


def test_a_minute_before_the_leak_began_sits_at_the_baseline() -> None:
    minutes = a_leaking_window(began_minutes_ago=30)

    assert minute_at(60, minutes).memory_used_bytes < BASELINE_MEMORY_BYTES * 1.1


def test_the_heap_is_higher_the_longer_the_leak_has_run() -> None:
    # A ramp, not a step. It is what separates this from every other scenario
    # here, and what a reader has to be able to date the start of.
    minutes = a_leaking_window(began_minutes_ago=30)

    early = minute_at(20, minutes).memory_used_bytes
    later = minute_at(10, minutes).memory_used_bytes

    assert later > early > BASELINE_MEMORY_BYTES


def test_the_heap_climbs_at_about_the_rate_it_says_it_does() -> None:
    # Pinned because everything downstream is calibrated against it: how long
    # the scenario takes to become diagnosable, and how long it has after that
    # before the shop is at its limit.
    minutes = a_leaking_window(began_minutes_ago=30)

    over_ten_minutes = (
        minute_at(5, minutes).memory_used_bytes
        - minute_at(15, minutes).memory_used_bytes
    )

    assert abs(over_ten_minutes - 10 * LEAK_CLIMB_BYTES_PER_MINUTE) < 40 * 1024**2


def test_the_heap_never_exceeds_the_limit_it_is_given() -> None:
    # A heap cannot be larger than it is allowed to be. What a shop at its
    # limit does is fail allocations, and that is reported as its own signal.
    minutes = a_leaking_window(began_minutes_ago=300)

    assert minute_at(0, minutes).memory_used_bytes <= MEMORY_LIMIT_BYTES


def test_a_scenario_that_is_not_about_memory_leaves_the_heap_alone() -> None:
    # The baseline has to be a baseline. A fixture that moved memory in every
    # scenario would leave a reader unable to say which one an incident is
    # about.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert minute_at(1, minutes).memory_used_bytes < BASELINE_MEMORY_BYTES * 1.1


def test_latency_is_untouched_while_the_heap_is_merely_large() -> None:
    # Half the limit is a larger heap and nothing else. A service reporting
    # latency for every megabyte it allocated would make memory undiagnosable
    # by making everything look like memory.
    minutes = a_leaking_window(began_minutes_ago=15)

    assert minute_at(0, minutes).p95_ms < 2 * a_calm_p95_in(minutes)


def test_latency_follows_the_heap_once_it_is_past_half_the_limit() -> None:
    minutes = a_leaking_window(began_minutes_ago=45)

    assert minute_at(0, minutes).p95_ms > 3 * a_calm_p95_in(minutes)


def test_the_error_rate_does_not_move_until_allocations_fail() -> None:
    # The whole shape of a leak, and why it gets paged on too late: the heap
    # climbs for the better part of an hour and latency follows it for half of
    # that, while the one signal most alert rules watch stays where it was.
    minutes = a_leaking_window(began_minutes_ago=45)

    assert minute_at(0, minutes).p95_ms > 3 * a_calm_p95_in(minutes)
    assert minute_at(0, minutes).error_rate < CLEARLY_HEALTHY


def test_a_shop_at_its_limit_finally_fails_requests() -> None:
    minutes = a_leaking_window(began_minutes_ago=60)

    assert minute_at(0, minutes).error_rate > CLEARLY_DEGRADED


def test_a_restart_reclaims_the_heap() -> None:
    restarted_at = SOME_NOW - timedelta(minutes=2)
    minutes = a_leaking_window(began_minutes_ago=40, restarts=(restarted_at,))

    assert minute_at(0, minutes).memory_used_bytes < BASELINE_MEMORY_BYTES * 1.2


def test_a_restart_leaves_the_climb_before_it_where_it_was() -> None:
    # A restart is a moment in the window, not a new beginning for the whole of
    # it. If the minutes before it flattened out, mitigating the incident would
    # erase it from the record it is diagnosed from.
    restarted_at = SOME_NOW - timedelta(minutes=2)
    minutes = a_leaking_window(began_minutes_ago=40, restarts=(restarted_at,))

    assert minute_at(5, minutes).memory_used_bytes > BASELINE_MEMORY_BYTES * 1.5


def test_the_heap_climbs_again_after_a_restart() -> None:
    # The fault is still in the code when the new process comes up. This is the
    # whole of why a restart mitigates a leak and does not resolve it.
    restarted_at = SOME_NOW - timedelta(minutes=10)
    minutes = a_leaking_window(began_minutes_ago=40, restarts=(restarted_at,))

    assert (
        minute_at(0, minutes).memory_used_bytes
        > minute_at(8, minutes).memory_used_bytes
    )


def test_a_restarted_minute_reports_the_process_that_came_up() -> None:
    # The only evidence that a restart landed. Memory falling is ambiguous on
    # its own - the process restarted, or the traffic dropped - and without a
    # start time that moved, a restart that never happened is indistinguishable
    # from one that happened and did not help.
    restarted_at = SOME_NOW - timedelta(minutes=2)
    minutes = a_leaking_window(began_minutes_ago=40, restarts=(restarted_at,))

    assert (
        minute_at(0, minutes).process_start_time_seconds
        > minute_at(5, minutes).process_start_time_seconds
    )


def test_a_leaking_minute_says_what_its_heap_is_doing() -> None:
    minutes = a_leaking_window(began_minutes_ago=45)

    said = " ".join(minute_at(0, minutes).log_lines)

    assert "heap at" in said
    assert "limit" in said


def test_a_shop_at_its_limit_says_allocations_are_failing() -> None:
    minutes = a_leaking_window(began_minutes_ago=60)

    assert "heap allocation failed" in " ".join(minute_at(0, minutes).log_lines)


def test_a_calm_shop_says_nothing_about_its_heap() -> None:
    # A service that logged its heap every minute would bury the minute it
    # mattered, and a reader scanning for the first mention of memory is doing
    # what a responder does.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert "heap" not in " ".join(minute_at(1, minutes).log_lines)


def test_the_minute_a_restart_landed_in_says_so_both_ways() -> None:
    # Two events, and a reader needs both: the process that was serving went
    # away, and another came up with the heap reclaimed. A restart that did not
    # reclaim would be a different incident.
    restarted_at = SOME_NOW - timedelta(minutes=2)
    minutes = a_leaking_window(began_minutes_ago=40, restarts=(restarted_at,))

    said = " ".join(minute_at(2, minutes).log_lines)

    assert "process terminated" in said
    assert "process started" in said


def test_a_scenario_with_no_flag_reports_no_flag_value() -> None:
    # Nothing here evaluated a flag, so nothing may say one did. A line naming
    # a flag would be the fixture handing an investigation a suspect it made up.
    minutes = a_leaking_window(began_minutes_ago=30)

    assert "dont-care-flag" not in " ".join(minute_at(1, minutes).log_lines)


def test_a_leaking_shop_still_serves_most_of_its_traffic() -> None:
    # A leak is not an outage until the very end, and even then it is thrashing
    # rather than down. A window that read as a total failure would be a
    # different incident with a different right answer.
    minutes = a_leaking_window(began_minutes_ago=30)

    assert minute_at(1, minutes).error_rate < CLEARLY_HEALTHY


def a_window_with_the_provider_down(began_minutes_ago: int,
                                    now: datetime = SOME_NOW) -> list[GeneratedMinute]:
    """A window in which the payment provider stopped answering that long ago.

    No flag and no leak, because neither is what is wrong: the whole point of
    the scenario is a window in which nothing Io owns has changed.
    """
    return generate(
        None,
        now,
        SOME_SPAN_MINUTES,
        provider_outage=ProviderOutage(began_at=now - timedelta(minutes=began_minutes_ago))
    )


def test_a_minute_before_the_provider_failed_reads_as_healthy() -> None:
    minutes = a_window_with_the_provider_down(began_minutes_ago=5)

    assert minute_at(10, minutes).error_rate < CLEARLY_HEALTHY


def test_every_page_fails_while_the_provider_is_refusing() -> None:
    # Every account page asks for the card, so a provider that answers none of
    # them fails all of them. That is what a hard dependency being down looks
    # like, and it is what makes this incident legible in one glance at a graph.
    minutes = a_window_with_the_provider_down(began_minutes_ago=5)

    assert minute_at(2, minutes).error_rate > CLEARLY_DEGRADED


def test_latency_moves_with_the_errors_rather_than_staying_flat() -> None:
    # Both signals together are this scenario's signature, and what tells it
    # apart from a bad flag - where the error rate moves and latency does not.
    minutes = a_window_with_the_provider_down(began_minutes_ago=5)

    calm = minute_at(10, minutes)
    failing = minute_at(2, minutes)

    assert failing.p95_ms > calm.p95_ms * 4
    assert failing.p50_ms > calm.p50_ms * 4


def test_the_heap_stays_where_it_was_while_the_provider_is_down() -> None:
    # Nothing is accumulating: an outage somewhere else is not a leak here, and
    # memory that moved with it would send a reader to restart the shop.
    minutes = a_window_with_the_provider_down(began_minutes_ago=5)

    calm = minute_at(10, minutes)
    failing = minute_at(2, minutes)

    assert abs(failing.memory_used_bytes - calm.memory_used_bytes) < MEMORY_LIMIT_BYTES // 10


def test_the_failures_name_the_provider_and_the_status_it_gave() -> None:
    # The host and the status are the evidence that the fault is not Io's, and
    # the log is the only channel carrying them.
    minutes = a_window_with_the_provider_down(began_minutes_ago=5)

    said = " ".join(minute_at(2, minutes).log_lines)

    assert PROVIDER_HOST in said
    assert "503" in said


def test_the_minute_the_outage_began_in_lands_between_the_two() -> None:
    # An outage starts mid-minute like everything else, so that minute is part
    # served and part failed. Reporting it as either whole would put a step
    # where the telemetry has a slope.
    minutes = a_window_with_the_provider_down(began_minutes_ago=5)

    began_in = minute_at(5, minutes)

    assert CLEARLY_HEALTHY < began_in.error_rate < 1.0


def test_a_scenario_with_no_outage_leaves_the_provider_answering() -> None:
    # Which is every other scenario: a window that failed pages for a provider
    # nobody staged would put a third party in every incident.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert PROVIDER_HOST not in " ".join(minute_at(5, minutes).log_lines)


SOME_CACHE_ENDPOINT = CacheEndpoint(host="cache.io-shop.svc.cluster.local", port=6380)


def a_window_with_the_cache_lost(began_minutes_ago: int) -> list[GeneratedMinute]:
    return generate(
        None,
        SOME_NOW,
        SOME_SPAN_MINUTES,
        flag="dont-care-flag",
        cache_endpoint=SOME_CACHE_ENDPOINT,
        cache_outage=CacheOutage(
            began_at=SOME_NOW - timedelta(minutes=began_minutes_ago)
        ),
    )


def a_window_with_a_working_cache() -> list[GeneratedMinute]:
    return generate(None, SOME_NOW, SOME_SPAN_MINUTES, flag="dont-care-flag",
                    cache_endpoint=SOME_CACHE_ENDPOINT)


def test_a_shop_with_no_cache_configured_reports_no_hit_ratio() -> None:
    # Absent rather than zero. Zero is what a cache answering nothing reports,
    # and a deployment without one has no fast path to have lost.
    minutes = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert minute_at(5, minutes).cache_hit_ratio is None


def test_a_working_cache_carries_most_of_the_traffic() -> None:
    minutes = a_window_with_a_working_cache()

    carried = minute_at(5, minutes).cache_hit_ratio

    assert carried is not None
    assert carried > 0.8


def test_losing_the_cache_takes_the_hit_ratio_to_nothing() -> None:
    minutes = a_window_with_the_cache_lost(began_minutes_ago=10)

    assert minute_at(3, minutes).cache_hit_ratio == 0.0


def test_losing_the_cache_moves_the_median_and_leaves_the_tail_alone() -> None:
    # The property the whole scenario exists for. Nine requests in ten were
    # served from cache, so the tail already described a recomputed page before
    # anything went wrong - losing the cache moves it from one miss to another.
    # The median steps from the cached path to the recomputed one.
    #
    # An incident visible only here is one a monitoring stack watching p95
    # never sees, which is exactly the case worth staging.
    minutes = a_window_with_the_cache_lost(began_minutes_ago=10)

    calm = minute_at(15, minutes)
    degraded = minute_at(3, minutes)

    assert degraded.p50_ms > calm.p50_ms * 4
    assert degraded.p95_ms < calm.p95_ms * 1.5


def test_losing_the_cache_does_not_move_the_error_rate() -> None:
    # The fallback is the designed behaviour: every page still renders, and
    # renders correctly. That is why nobody is paged.
    minutes = a_window_with_the_cache_lost(began_minutes_ago=10)

    assert minute_at(3, minutes).error_rate < CLEARLY_HEALTHY


def test_an_unreachable_cache_names_its_endpoint_in_the_logs() -> None:
    # The port set against the values file is the diagnosis.
    minutes = a_window_with_the_cache_lost(began_minutes_ago=10)

    said = " ".join(minute_at(3, minutes).log_lines)

    assert "cache.io-shop.svc.cluster.local" in said
    assert "6380" in said


def test_a_working_cache_says_nothing_about_itself() -> None:
    # Quiet while it works, for the reason the heap lines are: a service that
    # reported a healthy cache every minute would bury the minute it stopped.
    minutes = a_window_with_a_working_cache()

    assert "summary cache" not in " ".join(minute_at(5, minutes).log_lines)


def test_the_cache_changes_nothing_for_a_scenario_that_stages_none() -> None:
    # The cache draws from a sequence of its own, so consulting it cannot move
    # a figure drawn from the shared one. This is what lets every other
    # scenario stay exactly as it was.
    without = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)
    again = generate(a_flag_on_since(10), SOME_NOW, SOME_SPAN_MINUTES)

    assert [(m.p50_ms, m.p95_ms, m.error_rate) for m in without] == \
           [(m.p50_ms, m.p95_ms, m.error_rate) for m in again]
