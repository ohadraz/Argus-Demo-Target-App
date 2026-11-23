from __future__ import annotations

from datetime import UTC, datetime, timedelta

from target_app import clock

EPOCH = datetime(2026, 11, 23, 9, 0, tzinfo=UTC).timestamp()


def test_at_the_epoch_the_stacks_clock_reads_the_epoch() -> None:
    assert clock.at(EPOCH, epoch=EPOCH, speed=10.0) == datetime.fromtimestamp(EPOCH, tz=UTC)


def test_the_stacks_clock_runs_speed_times_as_fast_as_the_wall_from_the_epoch() -> None:
    six_real_seconds_on = clock.at(EPOCH + 6, epoch=EPOCH, speed=10.0)

    assert six_real_seconds_on - clock.at(EPOCH, epoch=EPOCH, speed=10.0) == timedelta(
        seconds=60
    )


def test_at_speed_one_the_stacks_clock_is_the_wall_whatever_the_epoch() -> None:
    real = EPOCH + 3600

    assert clock.at(real, epoch=EPOCH, speed=1.0) == datetime.fromtimestamp(real, tz=UTC)


def test_the_stacks_clock_reads_in_utc() -> None:
    assert clock.now().tzinfo is UTC
