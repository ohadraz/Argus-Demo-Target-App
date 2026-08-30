from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.rollout import CANARY_SHARE
from target_app.settings import get_unleash_settings

"""Telemetry generated from live flag state, at the moment it is asked for.

The service's metrics and logs are a pure function of two things: what time it
is, and when the flag went on and off. Nothing runs between requests - no
ticker, no rolling buffer, no traffic generator - and yet the answer tracks the
flag, because the answer is recomputed every time anybody asks.

That is what makes an incident here recoverable. A fixture authored in advance
describes a past that has already finished; this describes a present that is
still going on, so turning the flag off genuinely ends it. Nobody has to tell
this module that the flag changed, and nobody has to be the one who changed it.

Per-minute determinism does the rest. Each minute seeds its own generator from
its own identity, so a minute that has already elapsed reads the same however
many times it is fetched, and two reads of the same incident can be compared.
Only the minute in progress moves between reads - it has more seconds in it
each time.
"""

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# How many account pages are actually rendered per minute to measure that
# minute. The reported request volume is far larger, and deliberately so:
# rendering the reported volume for real would mean hundreds of thousands of
# calls per `/metrics` request across the metrics window, to produce the same
# rate a sample produces. This is what a sampled measurement is.
#
# The size is a noise decision, not a cost one. A sample of 50 resolves the
# error rate only to the nearest 2%, which makes a calm baseline read as a
# jagged 0-4% and gives a flag-on minute a spread wide enough to notice. At 200
# the baseline is smooth enough to be a baseline, and the whole 90-minute window
# still generates in well under a second.
_SAMPLE_SIZE = 200
_REPORTED_VOLUME_PER_MINUTE = 1200

# How many of a shop's registered shoppers have bought anything in the current
# month. Most have not, which is what a real customer base looks like - and it
# is why the monthly summary fails for most of the canary's traffic rather than
# for a handful of unlucky new accounts. Together with the canary share it sets
# the incident's error rate at roughly a third.
_ACTIVE_THIS_MONTH_SHARE = 0.2

# A minute's worth of log lines, at the density a sampled pipeline emits: a
# couple of representative failures, then one aggregate. Returning every failure
# would put thousands of near-identical lines into every window a reader asks
# for, and bury the two that say anything.
_MAX_FAILURE_LINES_PER_MINUTE = 2

# The service's healthy behaviour, and how much it wobbles minute to minute. The
# wobble matters: a baseline with no spread at all is not a baseline anything
# can be measured as departing from.
_BASELINE_ERROR_RATE = 0.01
_BASELINE_ERROR_RATE_WOBBLE = 0.005
_BASELINE_P50_MS = 45
_BASELINE_P95_MS = 215
_LATENCY_WOBBLE_MS = 8

_SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class FlagTimeline:
    """When the flag went on, and when it went off if it has.

    The whole of the generator's state. `turned_off_at` being `None` means the
    flag is still on - so an incident with no end recorded is one still
    happening, which is exactly the reading a caller wants.
    """

    turned_on_at: datetime
    turned_off_at: datetime | None = None

    def was_on_during(self, minute: datetime) -> bool:
        """Whether the flag was on for the whole of this minute."""
        if minute < self.turned_on_at.replace(second=0, microsecond=0):
            return False

        if self.turned_off_at is None:
            return True

        return minute < self.turned_off_at.replace(second=0, microsecond=0)

    def seconds_on_within(self, minute: datetime, elapsed_seconds: int) -> int:
        """How many of this minute's first `elapsed_seconds` the flag was on for.

        The in-progress minute needs this and whole minutes do not, because a
        revert lands mid-minute: the seconds before it are failing and the
        seconds after it are not, and a bucket claiming either for the whole
        minute would be wrong in the direction that matters most - the one
        mitigation reads to decide whether it worked.
        """
        window_start = minute
        window_end = minute + timedelta(seconds=elapsed_seconds)

        on_from = max(window_start, self.turned_on_at)
        on_until = window_end if self.turned_off_at is None else min(
            window_end, self.turned_off_at
        )

        return max(0, int((on_until - on_from).total_seconds()))


@dataclass(frozen=True)
class GeneratedMinute:
    """One minute, as both channels see it.

    Logs and metrics are produced together from the same outcomes rather than
    derived separately from the same inputs. Two derivations can disagree; one
    set of outcomes cannot.
    """

    minute_id: str
    error_rate: float
    p50_ms: int
    p95_ms: int
    request_volume: int
    log_lines: tuple[str, ...]


def generate(timeline: FlagTimeline,
             now: datetime,
             span_minutes: int,
             flag: str | None = None,
             breaks_when_flag_is_on: bool = True,
             decoy_flag: str | None = None,
             decoy_timeline: FlagTimeline | None = None) -> list[GeneratedMinute]:
    """Every minute from `span_minutes` ago up to and including the one in
    progress, once any of it has happened.

    The last entry is partial by construction - it covers only the seconds of
    the current minute that have actually happened. That is what real monitoring
    reports, and it is what lets a reverted flag show up as recovery within
    seconds instead of at the next minute boundary.

    `timeline` records when the shop was *broken*, not when the flag was on.
    The two coincide for a flag that breaks things by being switched on, and
    are opposites for one that breaks things by being switched off - which is
    what `breaks_when_flag_is_on` is for. It changes nothing about the fault,
    which is the same fault either way; it decides only what the logs report the
    flag as reading, and reporting that backwards would put a lie in the one
    channel a reader has for telling which way the flag moved.

    `decoy_flag` is a second flag whose value is reported beside the first and
    which decides nothing. It has its own timeline because it has its own
    history: it moved when the incident began, and it moves again the moment
    somebody reverts it - which, being a coincidence rather than a cause,
    changes no metric at all. A reader that saw the decoy frozen after being
    reverted would be reading a log that disagrees with the provider.
    """
    current_minute = now.replace(second=0, microsecond=0)
    elapsed_in_current = int((now - current_minute).total_seconds())
    named_flag = flag or get_unleash_settings().flag

    minutes = [
        _generate_minute(
            timeline,
            current_minute - timedelta(minutes=offset),
            elapsed_seconds=_SECONDS_PER_MINUTE,
            flag=named_flag,
            breaks_when_flag_is_on=breaks_when_flag_is_on,
            decoy_flag=decoy_flag,
            decoy_timeline=decoy_timeline,
        )
        for offset in range(span_minutes, 0, -1)
    ]
    # Not while zero whole seconds of it have happened. Nothing has been served
    # yet, so the share below is 0.0 by its own guard, and the minute would be
    # reported as calm to a shop that is on fire - a row that appears at 0.0%
    # and corrects itself on the next poll. One elapsed second is enough: the
    # sample is a fixed size and the rate is a share of it, so a barely-started
    # minute reports the same number a whole one does. A zero-second minute is
    # not a partial reading, it is no reading.
    if elapsed_in_current > 0:
        minutes.append(
            _generate_minute(
                timeline,
                current_minute,
                elapsed_seconds=elapsed_in_current,
                flag=named_flag,
                breaks_when_flag_is_on=breaks_when_flag_is_on,
                decoy_flag=decoy_flag,
                decoy_timeline=decoy_timeline,
            )
        )

    return minutes


def _generate_minute(
    timeline: FlagTimeline,
    minute: datetime,
    elapsed_seconds: int,
    flag: str,
    breaks_when_flag_is_on: bool,
    decoy_flag: str | None = None,
    decoy_timeline: FlagTimeline | None = None,
) -> GeneratedMinute:
    minute_id = minute.strftime(TIMESTAMP_FORMAT)
    entropy = random.Random(minute_id)

    seconds_on = timeline.seconds_on_within(minute, elapsed_seconds)
    share_of_minute_flagged = (
        seconds_on / elapsed_seconds if elapsed_seconds > 0 else 0.0
    )

    outcomes = [
        _serve_one_account_page(entropy, share_of_minute_flagged)
        for _ in range(_SAMPLE_SIZE)
    ]
    failures = [served.failure for served in outcomes if served.failure is not None]
    served_the_broken_path = sum(1 for served in outcomes if served.flag_is_on)
    evaluated_on = (
        served_the_broken_path
        if breaks_when_flag_is_on
        else len(outcomes) - served_the_broken_path
    )

    return GeneratedMinute(
        minute_id=minute_id,
        error_rate=round(len(failures) / _SAMPLE_SIZE, 4),
        # Latency is untouched by this fault, and that is load-bearing: an
        # error-rate departure with flat latency is what distinguishes a bad
        # flag from a bad deploy, and a reader that cannot tell them apart is
        # reading the alert rather than the evidence.
        p50_ms=_BASELINE_P50_MS + entropy.randint(-_LATENCY_WOBBLE_MS, _LATENCY_WOBBLE_MS),
        p95_ms=_BASELINE_P95_MS + entropy.randint(-_LATENCY_WOBBLE_MS, _LATENCY_WOBBLE_MS),
        request_volume=_REPORTED_VOLUME_PER_MINUTE,
        log_lines=_log_lines_for(
            minute_id,
            failures,
            len(outcomes),
            evaluated_on,
            flag,
            decoy=_decoy_evaluation_line(
                minute_id, minute, elapsed_seconds, len(outcomes),
                decoy_flag, decoy_timeline,
            ),
        ),
    )


def _decoy_evaluation_line(
    minute_id: str,
    minute: datetime,
    elapsed_seconds: int,
    sample_size: int,
    decoy_flag: str | None,
    decoy_timeline: FlagTimeline | None,
) -> str | None:
    """The decoy flag's value over this minute, or `None` when there is no decoy.

    Reported exactly as the staged flag's value is - the same line, from the
    same kind of measurement - because a reader has no way to tell which of two
    changes is the cause, and a decoy that announced itself in the logs would
    be answering the question the incident is asking.
    """
    if decoy_flag is None or decoy_timeline is None:
        return None

    seconds_on = decoy_timeline.seconds_on_within(minute, elapsed_seconds)
    share_on = seconds_on / elapsed_seconds if elapsed_seconds > 0 else 0.0

    return _flag_evaluation_line(
        minute_id, sample_size, round(share_on * sample_size), decoy_flag
    )


@dataclass(frozen=True)
class _ServedPage:
    """What serving one account page produced: how the flag evaluated for it,
    and the failure's own words if it failed.

    The flag value is carried out rather than discarded because it is what the
    request was actually decided by, and a service that routes on a flag logs
    the value it routed on.
    """

    flag_is_on: bool
    failure: str | None


def _serve_one_account_page(
    entropy: random.Random, share_of_minute_flagged: float
) -> _ServedPage:
    """Puts one request through the shop and records how it went.

    The two decisions above the call are the ones a real request would arrive
    with already made - which cohort the flag evaluated to for this shopper, and
    whether they fall inside the rollout's canary share. The shop itself is
    handed the answer, exactly as `io_shop.account_page` is handed it in
    production; nothing about the page's behaviour is simulated here.

    The flag is not consulted per request - `share_of_minute_flagged` already
    carries how much of this minute it was on for, so a minute the flag was on
    for half of routes half as much traffic to the canary as one it was on
    throughout. That is the whole mechanism by which a mid-minute revert shows
    up as a falling error rate.
    """
    flag_is_on = entropy.random() < share_of_minute_flagged
    use_monthly_summary = flag_is_on and entropy.random() < CANARY_SHARE

    page = serve_account_page(
        _an_account(entropy), use_monthly_summary=use_monthly_summary
    )

    if page.failure is not None:
        return _ServedPage(flag_is_on, page.failure)

    if entropy.random() < _BASELINE_ERROR_RATE + entropy.uniform(
        -_BASELINE_ERROR_RATE_WOBBLE, _BASELINE_ERROR_RATE_WOBBLE
    ):
        # Every real service fails a little without anything being wrong. Some
        # baseline noise is what makes "departed from baseline" a judgement
        # rather than a comparison against zero.
        return _ServedPage(
            flag_is_on, "UpstreamTimeout: payment authorization timed out"
        )

    return _ServedPage(flag_is_on, None)


def _an_account(entropy: random.Random) -> Account:
    """One shopper's history, as the account page loads it.

    Most accounts have bought nothing *this month*, which is the ordinary shape
    of a customer base rather than an edge case being contrived: a shop has far
    more registered shoppers than active ones in any given month. That is why
    the monthly summary fails for most of the traffic it is shown to, and why a
    stat scoped to a period is such a good hiding place for an empty-set bug.
    """
    prices = [entropy.randrange(500, 9000) for _ in range(entropy.randint(1, 12))]
    bought_this_month = entropy.random() < _ACTIVE_THIS_MONTH_SHARE

    if bought_this_month:
        purchases_this_month = entropy.randint(1, len(prices))
    else:
        purchases_this_month = 0

    return Account(
        purchases=tuple(
            Purchase(price_cents=price, in_current_month=index < purchases_this_month)
            for index, price in enumerate(prices)
        ),
        total_cents=sum(prices),
        total_this_month_cents=sum(prices[:purchases_this_month]),
    )


def _log_lines_for(
    minute_id: str,
    failures: list[str],
    sample_size: int,
    evaluated_on: int,
    flag: str,
    decoy: str | None = None,
) -> tuple[str, ...]:
    evaluations = (
        _flag_evaluation_line(minute_id, sample_size, evaluated_on, flag),
        *((decoy,) if decoy is not None else ()),
    )

    if not failures:
        return (
            *evaluations,
            f"{minute_id} INFO io-shop: account pages rendered normally",
        )

    quoted = [
        f"{minute_id} ERROR io-shop: account page request failed - {failure}"
        for failure in failures[:_MAX_FAILURE_LINES_PER_MINUTE]
    ]
    rate_percent = round(100 * len(failures) / sample_size)
    aggregate = (
        f"{minute_id} WARN io-shop: account page error rate at {rate_percent}% "
        f"over the last minute"
    )

    return (*evaluations, *quoted, aggregate)


def _flag_evaluation_line(
    minute_id: str, sample_size: int, evaluated_on: int, flag: str
) -> str:
    """What the flag evaluated to over this minute's traffic.

    An observation, not a conclusion: it reports the value the routing decision
    was made on, the way any service running a rollout reports which cohort it
    served. It never says the flag was *toggled* - that inference belongs to
    whoever reads the value changing between one minute and the next, beside an
    error rate that changed with it.

    A minute is usually wholly on or wholly off. It is mixed only when the flag
    moved partway through, which is exactly the minute a reader most wants to
    see both halves of.
    """
    evaluated_off = sample_size - evaluated_on

    if evaluated_on == 0:
        state = "off"
    elif evaluated_off == 0:
        state = "on"
    else:
        state = f"off {evaluated_off} / on {evaluated_on}"

    return (
        f"{minute_id} INFO io-shop: {flag}={state} - "
        f"{sample_size} evaluations"
    )


def utc_now() -> datetime:
    return datetime.now(UTC)
