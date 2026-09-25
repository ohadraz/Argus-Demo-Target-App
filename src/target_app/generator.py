from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from io_shop.account_page import serve_account_page
from io_shop.accounts import Account, Purchase
from io_shop.monthly_statement import StatementPeriod, period_for
from io_shop.payment_provider import AskTheProvider, ProviderAnswer, StoredCard
from io_shop.pricing_service import AskThePricingService, PricingAnswer
from io_shop.rollout import CANARY_SHARE
from io_shop.summary_cache import CacheAnswer, CacheEndpoint, LookUpSummary
from target_app.settings import get_unleash_settings

"""Telemetry generated from live state, at the moment it is asked for.

The service's metrics and logs are a pure function of what time it is and of
whatever condition is staged - when the flag went on and off, when the heap
began climbing, when the payment provider stopped answering. Nothing runs
between requests - no ticker, no rolling buffer, no traffic generator - and yet
the answer tracks the world, because the answer is derived from that world at
the moment anybody asks.

That is what makes an incident here recoverable. A fixture authored in advance
describes a past that has already finished; this describes a present that is
still going on, so turning the flag off genuinely ends it. Nobody has to tell
this module that the flag changed, and nobody has to be the one who changed it.

Per-minute determinism does the rest. Each minute seeds its own generator from
its own identity, so a minute that has already elapsed reads the same however
many times it is fetched, and two reads of the same incident can be compared.
Only the minute in progress moves between reads - it has more seconds in it
each time.

That determinism is also why a finished minute is *remembered* rather than
worked out again - see `_a_whole_minute`. It is an optimisation and nothing
more: the answer is the same either way, because the property above says it
must be. What it buys is the window's length. A six-hour window is 360 minutes
and every one of them renders account pages for real, so recomputing the lot on
every `/metrics` and every `/logs` call made the window a cost rather than a
setting - and the window has to be six hours, because that is what a responder
actually asks this service for.
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
# the baseline is smooth enough to be a baseline, and a whole six-hour window
# still generates in under a second - once, after which it is remembered.
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
# What the slowest one request in a hundred costs when nothing is wrong.
#
# Given its own baseline rather than pinned to a multiple of the p95, because a
# tail that can only move when the p95 moves is a fifth series saying what the
# fourth already said - and the whole reason to report a tail is that a fault
# reaching three requests in a hundred moves it and moves nothing else.
#
# Reported on every minute of every scenario, not only the ones about the tail,
# for the reason memory is: a series that appeared when it mattered would be
# read as a signal by its presence, and a quantile nobody has seen quiet is not
# one anything can be said to have departed from.
_BASELINE_P99_MS = 380
# How far each latency figure wobbles minute to minute, as a fraction of the
# figure itself rather than as a count of milliseconds. One absolute spread
# across both quantiles is a different claim about each: ±8ms is 4% of a 215ms
# tail and 18% of a 45ms median, which made the median by far the noisiest
# series the shop reports. Nothing about a real service works that way, and the
# consequence was concrete - a detector reading the median found departures in
# the noise, and dated a flag incident a minute before the flag moved.
#
# The tail's fraction is the larger of the two, which is the ordinary shape: a
# median is an average shopper's page and moves only when the service does,
# where a tail is whichever requests were unluckiest that minute and moves on
# its own. Getting this the right way round is what makes the median worth
# reading at all.
_MEDIAN_WOBBLE_AS_FRACTION_OF_BASELINE = 0.02
_TAIL_WOBBLE_AS_FRACTION_OF_BASELINE = 0.04

# What the shop's memory looks like when nothing is eating it: a working set a
# little over a fifth of the limit, wobbling the way a garbage-collected
# service's does between collections. Reported on every minute of every
# scenario, not only the ones about memory - a field that appeared when it
# mattered would be a field read as a signal by its presence, and a baseline
# nobody can see is not a baseline.
MEMORY_LIMIT_BYTES = 2 * 1024**3
BASELINE_MEMORY_BYTES = 440 * 1024**2
_MEMORY_WOBBLE_BYTES = 12 * 1024**2

# How long a shop nobody has restarted has been up. Further back than any
# window served here reaches, on purpose: a start time *inside* the window
# reads as a restart during the incident, which is the one thing this field
# exists to report.
#
# Twice the window rather than a little over it. This was six hours while the
# generated span was ninety minutes, and lengthening that span to six hours
# put the two exactly level - a settled shop's start time landed on the
# window's earliest minute, which is the one position that reads as "it came
# up just before all this". Clear of the window by the window's own length
# leaves no such reading, and leaves room for the span to grow again without
# quietly re-creating it.
SETTLED_UPTIME = timedelta(hours=12)

# What the payment provider answers with when it is not answering. A status
# rather than a dropped connection, because a status is what the shop's failure
# line quotes and what tells a reader the provider was reachable and refusing.
_PROVIDER_IS_UNAVAILABLE = 503
# What it answers with when it is well.
_PROVIDER_ANSWERED = 200
# What the provider hands back when it is well. One card for every shopper: the
# page shows the last four digits, and which four they are decides nothing.
_A_CARD = StoredCard(brand="visa", last_four="4242")

# How long a request spends waiting on a provider that is not answering before
# giving up. The shop's own work is tens of milliseconds, so a minute spent
# waiting on somebody else is visible in the latency long before anybody reads
# a log - which is what makes errors *and* latency this scenario's signature.
_PROVIDER_TIMEOUT_MS = 2000

# What the basket comes to. One figure for every shopper, for the reason one
# card serves every shopper: the page shows it and no metric reads it.
_A_BASKET_TOTAL_CENTS = 8400

# How long a call to the pricing service takes when that service is well. Well
# under the threshold the shop remarks on, so an ordinary minute carries no line
# about pricing at all - a fixture that mentioned a dependency every minute
# would make the minute it mattered unfindable.
_PRICING_CALL_MS = 20

# And how long it takes when that service is not. The pricing service answers
# every call throughout - nothing here fails - so this is a wait rather than a
# timeout, and the shop pays it on every request because every account page
# shows a basket total.
#
# Large enough that the median moves by an order of magnitude and small enough
# that no request looks like it gave up: a page taking one and a half seconds is
# a slow page, and a page taking thirty is a broken one. The scenario's whole
# claim is that nothing is broken.
_PRICING_SLOW_CALL_MS = 1500

# What one account page costs, by which path it took. The cached path still
# calls the payment provider and still renders; what it skips is walking the
# shopper's whole purchase history, which is the expensive part and the reason
# there is a cache at all.
_CACHED_PAGE_MS = 25
_RECOMPUTED_PAGE_MS = 190
# How much requests on the same path differ from each other within a minute.
_PAGE_LATENCY_WOBBLE_MS = 12

# How much of the shop's traffic the cache carries while it is reachable.
#
# Load-bearing, and the one number this scenario's detectability rests on. At
# nine in ten, the tail already describes a recomputed page *before* anything
# goes wrong - the slowest one in twenty is a miss either way - so losing the
# cache moves the tail from one miss to another and barely registers, while the
# median steps from the cached path to the recomputed one and multiplies
# sevenfold. That asymmetry is the incident: the aggregate a monitoring stack
# watches most confidently is the one that does not see this.
#
# Raise it past about nineteen in twenty and the tail moves too, which makes
# the scenario ordinary and costs it the only thing it demonstrates.
_HEALTHY_HIT_SHARE = 0.9

# How much of the shop's traffic the newest feature is rolled out to, and the
# number the tail scenario's whole claim rests on.
#
# The band is narrow and both edges are arithmetic. The 95th percentile of a
# two-hundred-request sample is the 190th value, so anything past five in a
# hundred displaces it and the incident becomes an ordinary latency regression
# that any monitoring stack sees. The 99th is the 198th, so anything under one
# in a hundred is invisible there too and there is no incident left to detect.
# Three in a hundred sits between them with room either side.
_SLOW_ROLLOUT_SHARE = 0.03

# Which requests of a minute's sample the rollout reached - an exact count,
# where every other condition here is a per-request draw.
#
# Deliberate, and the reason is arithmetic rather than taste. At three in a
# hundred of two hundred requests a binomial draw has a mean of six and a spread
# of about two and a half: four minutes in a hundred draw eleven or more and
# show the incident in the p95, and six in a hundred draw two or fewer and hide
# it from the p99. A scenario whose entire claim is that the p95 never sees this
# cannot be wrong about it one minute in twenty-five.
#
# It is also the more faithful of the two. A percentage rollout in a real
# provider buckets by user hash; it does not flip a coin per request. And the
# precedent is already here - `_with_the_allocations_that_failed` takes an exact
# share of the sample for the same kind of reason.
#
# What the rollout costs is not a constant, because the expensive path's cost
# follows the shopper: it collects the prices once and then walks what is left
# again per item, so one of these pages takes a recompute *plus* a recompute
# for every item bought. The slowest pages therefore belong to the heaviest
# accounts, which is what a tail is - not "three in a hundred requests are
# slow" but "the slowest requests belong to the best customers" - and the
# cheapest of them, a shopper who has bought one thing, still costs twice what
# an ordinary miss costs.
_RECOMPUTE_PER_ITEM_MS = _RECOMPUTED_PAGE_MS

# What a revision that recomputes the lifetime total once per purchase costs the
# shop it is deployed to, as a multiple of what the revision before it cost.
#
# A multiple over the baseline quantiles rather than a per-request centre, and
# that is the honest arithmetic for this scenario rather than a shortcut: the
# shop this deployment lands on has no summary cache configured, so every page
# already computes its own figure and there is no mixture of a fast path and a
# slow one for percentiles to fall between. Every request pays the same
# multiplier, so the median, the 95th and the 99th all move by it together -
# which is the property that separates this incident from the rollout's.
#
# Ten, because that is roughly what walking a history of a dozen purchases once
# per purchase costs against dividing a total that was already summed: the
# quadratic term is small at a dozen items and the constant factor is not. It
# takes the median from 45ms to around half a second and the tail from 380ms to
# nearly four seconds - unmissable on every panel a reader has, which is the
# point. Attribution is what is hard here, never detection.
_DEPLOY_SLOWDOWN = 10.0

# What the cache hands back when it holds a shopper's figure. Which figure it
# is decides nothing - the page shows it and no metric reads it - and a cached
# value disagreeing with a recomputed one would be a staleness bug this
# scenario is not about.
_A_CACHED_FIGURE_CENTS = 2400

# How fast a leaking shop's heap grows. Fast enough that the climb is a climb
# within a few minutes of anybody looking, and slow enough that the whole of it
# fits in the window: from the baseline this reaches the limit in a little under
# an hour, which is longer than a demo and shorter than a shift.
LEAK_CLIMB_BYTES_PER_MINUTE = 30 * 1024**2

# Where a heap stops being merely large. Below this the collector keeps up and
# nothing outside notices; above it, it runs more or less constantly and every
# request waits behind it - which is why latency follows memory rather than
# arriving with it.
_PRESSURE_BEGINS_AT = 0.5
# Where the shop stops serving. Allocations start failing outright, and the
# error rate finally moves - last of the three signals, which is what makes a
# leak so easy to page on too late.
_FAILING_BEGINS_AT = 0.9
# How much slower the shop is at the limit than at rest. Nine times over a
# 215ms p95 is the better part of two seconds, which is what a service spending
# its time in the collector actually looks like.
_SLOWEST_UNDER_PRESSURE = 9.0
# What share of requests fail once allocations do. Not all of them: a shop at
# its limit is thrashing, not down, and an outage would be a different incident.
_FAILING_SHARE = 0.35

_OUT_OF_MEMORY_FAILURE = "OutOfMemoryError: heap allocation failed"

_SECONDS_PER_MINUTE = 60
_BYTES_PER_MIB = 1024**2


@dataclass(frozen=True)
class FlagTimeline:
    """When the flag went on, and when it went off if it has.

    One of the three conditions a scenario can stage, beside a climbing heap
    and a provider that stopped answering. `turned_off_at` being `None` means
    the flag is still on - so an incident with no end recorded is one still
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
class ProviderOutage:
    """When the payment provider stopped answering, and when it started again.

    Shaped like the flag's timeline and kept apart from it, because they are
    timelines of different things: one is a value somebody set on Io's own
    provider and can set back, the other is another company's service being
    down. Nothing Io does moves this one, which is the entire point of the
    scenario it stages.

    `ended_at` being `None` means the provider is still down - an outage with no
    end recorded is one still going on.
    """

    began_at: datetime
    ended_at: datetime | None = None

    def share_of(self, minute: datetime, elapsed_seconds: int) -> float:
        """How much of this minute's first `elapsed_seconds` the provider spent
        refusing.

        A share rather than a count of seconds, because it is what both of the
        things that follow are scaled by: how many of the minute's requests
        failed, and how long the minute's requests spent waiting. The minute an
        outage begins in is partly served and partly failed, and reporting it as
        either whole would put a step where the telemetry has a slope.
        """
        if elapsed_seconds <= 0:
            return 0.0

        window_end = minute + timedelta(seconds=elapsed_seconds)
        down_from = max(minute, self.began_at)
        down_until = window_end if self.ended_at is None else min(
            window_end, self.ended_at
        )
        seconds_down = max(0.0, (down_until - down_from).total_seconds())

        return min(1.0, seconds_down / elapsed_seconds)


@dataclass(frozen=True)
class CacheOutage:
    """When the shop stopped being able to reach its cache, and when it could
    again.

    Shaped like the provider's outage and meaning something different. That one
    is another company's service being down; this one is Io's own cache, which
    is up the whole time - what broke is the address the shop was told to dial.
    So there is nothing wrong with the cache, nothing wrong with the code, and
    the thing to put back is a value in a file.

    Named for the outage rather than for the address, because `CacheEndpoint`
    is already the address and the two are different facts: the endpoint says
    where the shop is dialling, and this says over which minutes dialling it
    got nowhere.

    `ended_at` being `None` means the shop still cannot reach it.
    """

    began_at: datetime
    ended_at: datetime | None = None

    def share_of(self, minute: datetime, elapsed_seconds: int) -> float:
        """How much of this minute's first `elapsed_seconds` the shop spent
        unable to reach the cache.

        A share for the reason the provider's is one: the minute a
        misconfiguration lands in is partly served from cache and partly not,
        and reporting it as either whole would put a step where the telemetry
        has a slope.
        """
        if elapsed_seconds <= 0:
            return 0.0

        window_end = minute + timedelta(seconds=elapsed_seconds)
        lost_from = max(minute, self.began_at)
        lost_until = window_end if self.ended_at is None else min(
            window_end, self.ended_at
        )
        seconds_lost = max(0.0, (lost_until - lost_from).total_seconds())

        return min(1.0, seconds_lost / elapsed_seconds)


@dataclass(frozen=True)
class SlowRollout:
    """When the shop began serving a few of its requests the expensive way, and
    when it stopped.

    Shaped like the two outages above and staging something that is not an
    outage at all: nothing here is failing, unreachable, or down. A feature went
    out to a slice of traffic, and for the shoppers in that slice the page does
    more work than it needs to. The pages are correct, the shop is up, and the
    only thing wrong is how long a few of them take.

    That is why it is its own condition rather than a share on the flag
    timeline. What the flag decides is which cohort a request is in; what this
    decides is over which minutes being in that cohort cost anything.

    `ended_at` being `None` means the rollout is still out there.
    """

    began_at: datetime
    ended_at: datetime | None = None

    def share_of(self, minute: datetime, elapsed_seconds: int) -> float:
        """How much of this minute's first `elapsed_seconds` the rollout was
        live for.

        A share for the reason the other two conditions have one: the minute a
        rollout lands in is partly served the old way and partly the new, and
        reporting it as either whole would put a step where the telemetry has a
        slope. Here it scales how many of the minute's requests reached the
        expensive path, so the onset ramps over one minute rather than
        appearing entire.
        """
        if elapsed_seconds <= 0:
            return 0.0

        window_end = minute + timedelta(seconds=elapsed_seconds)
        live_from = max(minute, self.began_at)
        live_until = window_end if self.ended_at is None else min(
            window_end, self.ended_at
        )
        seconds_live = max(0.0, (live_until - live_from).total_seconds())

        return min(1.0, seconds_live / elapsed_seconds)


@dataclass(frozen=True)
class SlowDeployment:
    """When the shop began serving *every* request the expensive way, and when
    it stopped.

    The same shape as `SlowRollout` and the opposite reach, which is the whole
    distinction between the two scenarios they stage. A rollout is a cohort: a
    few requests in a hundred take a longer path and the rest are untouched, so
    the incident lives where a small share of the traffic lives - the far tail.
    A deployment is everybody: the revision that is running is the revision
    every request runs, so the median moves with the tail and nothing hides.

    Kept as a condition of its own rather than derived from the deploy history
    for the reason the cache outage is: what the history records is that a
    revision went out, and what this records is over which minutes running it
    cost anything. A rollback ends the stretch without removing the entry.

    `ended_at` being `None` means the slower revision is still deployed.
    """

    began_at: datetime
    ended_at: datetime | None = None

    def share_of(self, minute: datetime, elapsed_seconds: int) -> float:
        """How much of this minute's first `elapsed_seconds` the slower revision
        was the one running.

        A share rather than a flag, as every other condition here reports
        itself: the minute a deployment lands in is served partly by each
        revision, so the onset ramps across it instead of stepping. The minute a
        rollback lands in is the same thing in reverse.
        """
        if elapsed_seconds <= 0:
            return 0.0

        window_end = minute + timedelta(seconds=elapsed_seconds)
        live_from = max(minute, self.began_at)
        live_until = window_end if self.ended_at is None else min(
            window_end, self.ended_at
        )
        seconds_live = max(0.0, (live_until - live_from).total_seconds())

        return min(1.0, seconds_live / elapsed_seconds)


@dataclass(frozen=True)
class PricingSlowdown:
    """When the pricing service began taking too long to answer, and when it
    stopped.

    The same shape as the payment provider's outage and a different kind of
    neighbour: that one is another company's service, and this is another team's
    in the same company. Nothing about the shape says which - the difference is
    who can be asked to fix it, and it is published in the service catalogue
    rather than modelled here.

    Two things separate it from the outage besides ownership. The service answers
    every call, so no request fails and the error rate never moves. And the shop
    has no timeout to give up at, because there is nothing to give up on: it
    waits, gets a correct price, and serves a correct page slowly.

    Kept as a condition of its own for the reason every other condition here is
    one: what it records is over which minutes calling the pricing service cost
    anything. A restart of that service ends the stretch.

    `ended_at` being `None` means it is still slow.
    """

    began_at: datetime
    ended_at: datetime | None = None

    def share_of(self, minute: datetime, elapsed_seconds: int) -> float:
        """How much of this minute's first `elapsed_seconds` the pricing service
        spent answering slowly.

        A share for the reason the provider's outage has one: the minute a
        degradation begins in is partly served at the old speed and partly at
        the new, and reporting it as either whole would put a step where the
        telemetry has a slope. The minute a restart lands in is the same thing
        in reverse.
        """
        if elapsed_seconds <= 0:
            return 0.0

        window_end = minute + timedelta(seconds=elapsed_seconds)
        slow_from = max(minute, self.began_at)
        slow_until = window_end if self.ended_at is None else min(
            window_end, self.ended_at
        )
        seconds_slow = max(0.0, (slow_until - slow_from).total_seconds())

        return min(1.0, seconds_slow / elapsed_seconds)


@dataclass(frozen=True)
class ProcessLifetime:
    """When the shop's process came up, and every time it has come up since.

    A leak is measured from whichever process is serving, so a restart has to
    be a moment in a history rather than a new value replacing the old one. A
    single "started at" that moved would flatten the climb retrospectively:
    the minutes before the restart would report the heap they would have had if
    the process had only just started, and the incident would vanish from the
    window the moment it was mitigated.
    """

    started_at: datetime
    restarts: tuple[datetime, ...] = ()

    def serving_during(self, minute: datetime) -> datetime:
        """When the process serving this minute came up.

        The latest start at or before the minute. A restart lands mid-minute
        and the minute it lands in is served mostly by the new process, so the
        minute takes the new start rather than splitting - a bucket carries one
        start time, and the one worth reporting is the one still serving when
        anybody reads it.
        """
        came_up = [
            moment.replace(second=0, microsecond=0)
            for moment in self.restarts
            if moment.replace(second=0, microsecond=0) <= minute
        ]

        return max(came_up) if came_up else self.started_at


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
    p99_ms: int
    request_volume: int
    memory_used_bytes: int
    memory_limit_bytes: int | None
    process_start_time_seconds: float
    log_lines: tuple[str, ...]
    # `None` where the deployment configured no cache, which is a different
    # fact from a cache answering nothing - see `_how_much_the_cache_carried`.
    cache_hit_ratio: float | None = None


def generate(timeline: FlagTimeline | None,
             now: datetime,
             span_minutes: int,
             flag: str | None = None,
             breaks_when_flag_is_on: bool = True,
             decoy_flag: str | None = None,
             decoy_timeline: FlagTimeline | None = None,
             process_started_at: datetime | None = None,
             leak_started_at: datetime | None = None,
             restarts: tuple[datetime, ...] = (),
             provider_outage: ProviderOutage | None = None,
             cache_endpoint: CacheEndpoint | None = None,
             cache_outage: CacheOutage | None = None,
             slow_rollout: SlowRollout | None = None,
             slow_deployment: SlowDeployment | None = None,
             pricing_slowdown: PricingSlowdown | None = None,
             ships_the_statement: bool = False) -> list[GeneratedMinute]:
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

    `timeline` is `None` for a scenario that stages no flag at all. Nothing is
    then routed to the canary and no flag value is reported, because there is
    no flag whose value could be reported - a line naming one would be the
    fixture inventing a suspect.

    `process_started_at` is when the serving process last came up, reported on
    every minute so that a restart is visible as a change in it. Left unsaid, it
    is taken to be further back than this window reaches, which is what a shop
    nobody has restarted looks like.

    `leak_started_at` is when the shop began retaining what it should have let
    go. From that minute the heap climbs, and everything the climb does to the
    service follows from it. Left unsaid, the shop's memory sits at its
    baseline, which is what every scenario that is not about memory looks like.

    `restarts` are the moments somebody brought the process back. Each one
    reclaims the heap and starts the climb again from the baseline, because the
    fault is still in the code when the new process comes up - which is exactly
    why a restart mitigates a leak and does not resolve it.

    `provider_outage` is the stretch the payment provider spent refusing. Left
    unsaid, it answers every request, which is what every scenario that is not
    about somebody else's outage looks like.

    `cache_endpoint` is where the deployment says the summary cache lives. Left
    unsaid, the shop has no cache at all: every page computes its figure, no
    hit ratio is reported, and latency is the baseline model every scenario
    used before there was a cache.

    `cache_outage` is the stretch the shop could not reach that endpoint over.
    Left unsaid, a configured cache answers throughout - which is what a shop
    with a working cache looks like, and what the minutes before this
    scenario's onset are.

    `slow_rollout` is the stretch over which a few of the shop's requests were
    served the expensive way. Left unsaid, nothing is rolled out and every page
    takes one of the two paths it always took. It needs a cache endpoint beside
    it, because a shop with no cache reports the baseline quantile model and a
    model has no sample for a percentile to be taken over - which is the whole
    of what this condition is visible in.

    `pricing_slowdown` is the stretch the pricing service spent answering
    slowly. Left unsaid, it answers promptly and nothing in the window mentions
    it - which is what every scenario that is not about a neighbour of Io's own
    looks like. It needs no cache endpoint beside it: every account page shows a
    basket total, so the wait lands on every request and the baseline quantile
    model carries it.

    `ships_the_statement` says which feature the flag is shipping this time.
    The shop has one feature flag and several scenarios behind it: most ship
    the monthly summary, one ships the typical purchase, and this one ships the
    monthly statement panel. Left unsaid, the flag ships the summary, which is
    what it shipped before there was anything else to choose.

    It changes no figure this generator reports. The statement breaks for the
    same shoppers the summary breaks for - a month with nothing bought in it
    has no largest purchase any more than it has an average - so the error rate
    and the latency are the summary scenario's. What it changes is the file the
    shop's log names as having raised the failure, and that file is the whole
    of what the scenario is for.
    """
    current_minute = now.replace(second=0, microsecond=0)
    elapsed_in_current = int((now - current_minute).total_seconds())
    named_flag = flag or get_unleash_settings().flag
    lifetime = ProcessLifetime(
        started_at=(
            process_started_at
            if process_started_at is not None
            else current_minute - SETTLED_UPTIME
        ),
        restarts=restarts,
    )

    minutes = [
        _a_whole_minute(
            timeline,
            current_minute - timedelta(minutes=offset),
            flag=named_flag,
            breaks_when_flag_is_on=breaks_when_flag_is_on,
            decoy_flag=decoy_flag,
            decoy_timeline=decoy_timeline,
            lifetime=lifetime,
            leak_started_at=leak_started_at,
            provider_outage=provider_outage,
            cache_endpoint=cache_endpoint,
            cache_outage=cache_outage,
            slow_rollout=slow_rollout,
            slow_deployment=slow_deployment,
            pricing_slowdown=pricing_slowdown,
            ships_the_statement=ships_the_statement,
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
                lifetime=lifetime,
                leak_started_at=leak_started_at,
                provider_outage=provider_outage,
                cache_endpoint=cache_endpoint,
                cache_outage=cache_outage,
                slow_rollout=slow_rollout,
                slow_deployment=slow_deployment,
                pricing_slowdown=pricing_slowdown,
                ships_the_statement=ships_the_statement,
            )
        )

    return minutes


# How many generated minutes to keep. A window is six hours, and a demo moves
# between a handful of scenarios in a sitting - so this holds several whole
# windows at once and still costs a few megabytes. Sized to be larger than the
# working set rather than tuned: an entry evicted while it is still being asked
# for is regenerated, which is exactly what used to happen every time.
_MINUTES_WORTH_REMEMBERING = 4096


@lru_cache(maxsize=_MINUTES_WORTH_REMEMBERING)
def _a_whole_minute(
    timeline: FlagTimeline | None,
    minute: datetime,
    flag: str,
    breaks_when_flag_is_on: bool,
    lifetime: ProcessLifetime,
    decoy_flag: str | None = None,
    decoy_timeline: FlagTimeline | None = None,
    leak_started_at: datetime | None = None,
    provider_outage: ProviderOutage | None = None,
    cache_endpoint: CacheEndpoint | None = None,
    cache_outage: CacheOutage | None = None,
    slow_rollout: SlowRollout | None = None,
    slow_deployment: SlowDeployment | None = None,
    pricing_slowdown: PricingSlowdown | None = None,
    ships_the_statement: bool = False,
) -> GeneratedMinute:
    """One minute that has finished, generated once and then remembered.

    The module's opening paragraph is what makes this sound rather than a
    gamble: a minute that has already elapsed reads the same however many times
    it is fetched, because it seeds its own generator from its own identity.
    That is a property this service already promised, and remembering the
    answer only stops it being recomputed to say the same thing.

    Only whole minutes. The minute in progress has more seconds in it each time
    anybody asks, which is the one thing here that is *meant* to change between
    reads - so it goes to `_generate_minute` directly, every request, for ever.

    The key is every value the minute is computed from, which is why they are
    all parameters and none of them is read from anywhere. A flag toggled now
    does not change a minute that has already finished, but it does change the
    timeline - so the next request misses, regenerates the window once, and
    hits from then on. That is the whole cost of a mitigation: one window, not
    one per request.

    What this is worth: a six-hour window is 360 minutes, and the account pages
    behind it are rendered for real. Regenerating all of them on every
    `/metrics` and every `/logs` call was most of what an e2e run spent its
    time doing.
    """
    return _generate_minute(
        timeline,
        minute,
        elapsed_seconds=_SECONDS_PER_MINUTE,
        flag=flag,
        breaks_when_flag_is_on=breaks_when_flag_is_on,
        lifetime=lifetime,
        decoy_flag=decoy_flag,
        decoy_timeline=decoy_timeline,
        leak_started_at=leak_started_at,
        provider_outage=provider_outage,
        cache_endpoint=cache_endpoint,
        cache_outage=cache_outage,
        slow_rollout=slow_rollout,
        slow_deployment=slow_deployment,
        pricing_slowdown=pricing_slowdown,
        ships_the_statement=ships_the_statement,
    )


def _generate_minute(
    timeline: FlagTimeline | None,
    minute: datetime,
    elapsed_seconds: int,
    flag: str,
    breaks_when_flag_is_on: bool,
    lifetime: ProcessLifetime,
    decoy_flag: str | None = None,
    decoy_timeline: FlagTimeline | None = None,
    leak_started_at: datetime | None = None,
    provider_outage: ProviderOutage | None = None,
    cache_endpoint: CacheEndpoint | None = None,
    cache_outage: CacheOutage | None = None,
    slow_rollout: SlowRollout | None = None,
    slow_deployment: SlowDeployment | None = None,
    pricing_slowdown: PricingSlowdown | None = None,
    ships_the_statement: bool = False,
) -> GeneratedMinute:
    minute_id = minute.strftime(TIMESTAMP_FORMAT)
    entropy = random.Random(minute_id)
    # A sequence of its own, so that consulting a cache on every request cannot
    # move a single figure drawn from the one above. Seeded from the same
    # minute, so this minute reads the same however often it is fetched.
    cache_entropy = (
        random.Random(f"{minute_id}-cache") if cache_endpoint is not None else None
    )

    seconds_on = (
        timeline.seconds_on_within(minute, elapsed_seconds)
        if timeline is not None
        else 0
    )
    share_of_minute_flagged = (
        seconds_on / elapsed_seconds if elapsed_seconds > 0 else 0.0
    )
    share_of_minute_refused = (
        provider_outage.share_of(minute, elapsed_seconds)
        if provider_outage is not None
        else 0.0
    )

    share_of_minute_without_the_cache = (
        cache_outage.share_of(minute, elapsed_seconds)
        if cache_outage is not None
        else 0.0
    )

    # What every request cost this minute because of the revision that is
    # deployed, as a multiple of what it cost before. Exactly 1.0 where no
    # slower revision is running, which is what leaves every other scenario's
    # figures where they were - and it draws no entropy, so it cannot shift a
    # single draw made after it either.
    slower_for_the_deploy = 1.0 + (_DEPLOY_SLOWDOWN - 1.0) * (
        slow_deployment.share_of(minute, elapsed_seconds)
        if slow_deployment is not None
        else 0.0
    )

    # How much of this minute the pricing service spent answering slowly.
    share_of_minute_waiting_on_pricing = (
        pricing_slowdown.share_of(minute, elapsed_seconds)
        if pricing_slowdown is not None
        else 0.0
    )

    # And how many of the minute's requests that share amounts to. An exact
    # count rather than a draw per request, for the reason the rollout's cohort
    # is one: the number is what the minute's own log line reports, and a figure
    # that wandered by a request or two either way would have the fixture
    # disagreeing with itself about how many pages waited.
    calls_that_waited = round(_SAMPLE_SIZE * share_of_minute_waiting_on_pricing)

    # An exact count of the sample rather than a draw per request - see
    # `_SLOW_ROLLOUT_SHARE`. Scaled by how much of the minute the rollout was
    # live for, so the minute it lands in is partly served each way and the
    # onset is a slope rather than a step, exactly as the two outages above are.
    reached_by_the_rollout = round(
        _SLOW_ROLLOUT_SHARE * _SAMPLE_SIZE * (
            slow_rollout.share_of(minute, elapsed_seconds)
            if slow_rollout is not None
            else 0.0
        )
    )

    outcomes = [
        _serve_one_account_page(
            entropy,
            share_of_minute_flagged,
            share_of_minute_refused,
            cache_entropy,
            share_of_minute_without_the_cache,
            cache_endpoint,
            the_rollout_reached_it=index < reached_by_the_rollout,
            the_flag_ships_the_slow_feature=slow_rollout is not None,
            the_flag_ships_the_statement=ships_the_statement,
            # The month this minute falls in, which is what a request arriving
            # in it would have been asking about. Taken from the minute rather
            # than from the clock, so that a window fetched twice reads the
            # same both times - the same reason every other figure here is
            # seeded from the minute's own id.
            statement_period=period_for(minute.month, minute.year),
            the_pricing_call_was_slow=index < calls_that_waited,
        )
        for index in range(_SAMPLE_SIZE)
    ]
    failures = [served.failure for served in outcomes if served.failure is not None]
    served_the_broken_path = sum(1 for served in outcomes if served.flag_is_on)
    evaluated_on = (
        served_the_broken_path
        if breaks_when_flag_is_on
        else len(outcomes) - served_the_broken_path
    )

    serving_since = lifetime.serving_during(minute)
    # Before the wobble, and deliberately: what the service around the heap
    # does follows the trend rather than the jitter, and a p95 that moved with
    # the collector's sawtooth would be noise dressed as a signal.
    heap_bytes = _the_heap_at(minute, leak_started_at, serving_since)
    pressure = heap_bytes / MEMORY_LIMIT_BYTES
    under_pressure = _how_much_slower_under(pressure)
    failures = _with_the_allocations_that_failed(failures, pressure)
    measured = sorted(
        served.latency_ms for served in outcomes if served.latency_ms is not None
    )

    return GeneratedMinute(
        minute_id=minute_id,
        error_rate=round(len(failures) / _SAMPLE_SIZE, 4),
        # A flag fault leaves latency alone, and that is load-bearing: an
        # error-rate departure with flat latency is what distinguishes a bad
        # flag from a bad deploy. A leak is the case where it does not stay
        # flat - the multiplier is 1 until the heap is over half the limit, so
        # every scenario that is not about memory reads exactly as it did.
        #
        # A shop with a cache reports the quantiles of what it actually served
        # instead. That is not a second latency model so much as the honest one:
        # a mixture of a fast path and a slow path has quantiles that cannot be
        # written down as a baseline and a multiplier, and the whole point of
        # this scenario is where in that mixture the 50th and the 95th fall.
        p50_ms=_the_quantile_at(measured, 0.50) if measured else round(
            (_BASELINE_P50_MS + _wobble_around(
                entropy, _BASELINE_P50_MS, _MEDIAN_WOBBLE_AS_FRACTION_OF_BASELINE
            ))
            * under_pressure
            * slower_for_the_deploy
            + _waiting_on_the_provider(share_of_minute_refused)
            + _waiting_on_the_pricing_service(share_of_minute_waiting_on_pricing)
        ),
        p95_ms=_the_quantile_at(measured, 0.95) if measured else round(
            (_BASELINE_P95_MS + _wobble_around(
                entropy, _BASELINE_P95_MS, _TAIL_WOBBLE_AS_FRACTION_OF_BASELINE
            ))
            * under_pressure
            * slower_for_the_deploy
            + _waiting_on_the_provider(share_of_minute_refused)
            + _waiting_on_the_pricing_service(share_of_minute_waiting_on_pricing)
        ),
        cache_hit_ratio=_how_much_the_cache_carried(outcomes),
        request_volume=_REPORTED_VOLUME_PER_MINUTE,
        # Drawn after the latencies, so that adding memory to the bucket left
        # every figure this generator already produced exactly where it was:
        # each minute seeds one generator, and a draw inserted earlier would
        # shift every draw after it.
        memory_used_bytes=min(
            MEMORY_LIMIT_BYTES,
            heap_bytes + entropy.randint(-_MEMORY_WOBBLE_BYTES, _MEMORY_WOBBLE_BYTES),
        ),
        memory_limit_bytes=MEMORY_LIMIT_BYTES,
        process_start_time_seconds=serving_since.timestamp(),
        # Out of field order, and after the memory draw on purpose. Each minute
        # seeds one generator, so a draw inserted beside the other two
        # quantiles would shift the memory wobble and move a figure in every
        # scenario that has nothing to do with the tail. Taken last, adding the
        # tail left every number this generator already produced exactly where
        # it was - which is the same reason memory itself is drawn after the
        # latencies.
        p99_ms=_the_quantile_at(measured, 0.99) if measured else round(
            (_BASELINE_P99_MS + _wobble_around(
                entropy, _BASELINE_P99_MS, _TAIL_WOBBLE_AS_FRACTION_OF_BASELINE
            ))
            * under_pressure
            * slower_for_the_deploy
            + _waiting_on_the_provider(share_of_minute_refused)
            + _waiting_on_the_pricing_service(share_of_minute_waiting_on_pricing)
        ),
        log_lines=_log_lines_for(
            minute_id,
            failures,
            len(outcomes),
            evaluated_on,
            flag if timeline is not None else None,
            decoy=_decoy_evaluation_line(
                minute_id, minute, elapsed_seconds, len(outcomes),
                decoy_flag, decoy_timeline,
            ),
            heap=_heap_lines_for(minute_id, minute, heap_bytes, pressure, lifetime),
            cache=_cache_lines_for(minute_id, outcomes),
            pricing=_pricing_lines_for(minute_id, outcomes),
        ),
    )


def _the_quantile_at(sorted_latencies: list[int], quantile: float) -> int:
    """The latency at this quantile of what the minute actually served.

    Nearest-rank, which is what a monitoring stack reporting a percentile over
    a sample does: the value at the position the quantile lands on, not an
    interpolation between two neighbours. A mixture of a fast path and a slow
    one has no meaningful value *between* them, and interpolating would invent
    latencies no request experienced.
    """
    position = max(0, min(len(sorted_latencies) - 1,
                          round(quantile * len(sorted_latencies)) - 1))

    return sorted_latencies[position]


def _how_much_the_cache_carried(outcomes: list[_ServedPage]) -> float | None:
    """The share of this minute's requests the cache answered, or `None` where
    the deployment has no cache.

    `None` rather than zero for a shop without one, because zero is what a
    cache answering nothing reports and the two are opposite situations: one
    has no fast path to lose and the other has just lost it.
    """
    if not any(served.latency_ms is not None for served in outcomes):
        return None

    return round(
        sum(1 for served in outcomes if served.from_cache) / len(outcomes), 4
    )


def _cache_lines_for(minute_id: str, outcomes: list[_ServedPage]) -> tuple[str, ...]:
    """What the shop said about its cache this minute.

    Quiet while it is answering, for the reason the heap lines are quiet while
    the heap is ordinary: a service that reported a working cache every minute
    would bury the minute it stopped working.

    One line rather than one per failed lookup. Every request failed the same
    way at the same address, and a minute of identical lines is a minute whose
    two informative words nobody reaches.
    """
    unreachable = [
        served.cache_failure for served in outcomes if served.cache_failure is not None
    ]

    if not unreachable:
        return ()

    return (
        f"{minute_id} ERROR io-shop: summary cache lookup failed - "
        f"{unreachable[0]} ({len(unreachable)} of {len(outcomes)} requests)",
    )


def _pricing_lines_for(minute_id: str, outcomes: list[_ServedPage]) -> tuple[str, ...]:
    """What the shop said about the pricing service this minute.

    Quiet while it answers in the time it always does, for the reason the cache
    lines are quiet while the cache works: a line every minute about a dependency
    behaving normally is a line nobody reads on the minute it stops.

    WARN rather than ERROR, and that is the whole character of this incident. No
    request failed, every page was correct, and every shopper was shown the right
    price - so a reader looking for errors finds none, and the only account of
    where the time went sits at a level most searches filter out.

    One line rather than one per slow call, as the cache's is: every call waited
    on the same service at the same address, and a minute of identical lines is a
    minute whose informative words nobody reaches. The count is what says how
    much of the minute was affected.
    """
    delayed = [
        served.pricing_delay for served in outcomes
        if served.pricing_delay is not None
    ]

    if not delayed:
        return ()

    return (
        (
            f"{minute_id} WARN io-shop: slow upstream call - "
            f"{delayed[0]} ({len(delayed)} of {len(outcomes)} requests)"
        ),
    )


def _the_heap_at(minute: datetime,
                 leak_started_at: datetime | None,
                 serving_since: datetime) -> int:
    """How much memory the shop is holding during this minute.

    The baseline until something starts retaining, then the baseline plus
    whatever has accumulated since - counted from the later of the leak
    beginning and the process coming up, because a new process starts with an
    empty heap however long the fault has been in the code.

    Capped at the limit. A heap cannot exceed the limit it is allowed; what a
    shop at its limit does is fail allocations, which is a different signal and
    is reported as one.
    """
    if leak_started_at is None:
        return BASELINE_MEMORY_BYTES

    climbing_since = max(leak_started_at, serving_since)
    minutes_climbing = max(
        0.0, (minute - climbing_since).total_seconds() / _SECONDS_PER_MINUTE
    )

    return min(
        MEMORY_LIMIT_BYTES,
        BASELINE_MEMORY_BYTES + int(minutes_climbing * LEAK_CLIMB_BYTES_PER_MINUTE),
    )


def _wobble_around(entropy: random.Random,
                   baseline_ms: int,
                   as_fraction_of_baseline: float) -> int:
    """How far this minute's figure sits from its baseline.

    One draw, so that expressing the spread as a fraction left the sequence of
    draws exactly as long as it was - each minute seeds one generator, and a
    draw added or removed shifts every draw after it.

    At least a millisecond, whatever the fraction works out to. Latency is
    reported in whole milliseconds, so a small enough baseline rounds its own
    spread away and reports the identical figure every minute - and a series
    with no spread at all is one every later reading departs from infinitely
    far, which is the one shape a baseline must never have.
    """
    spread = max(1, round(baseline_ms * as_fraction_of_baseline))

    return entropy.randint(-spread, spread)


def _how_much_slower_under(pressure: float) -> float:
    """How much longer a request takes at this much of the limit.

    Nothing at all until the heap is over half the limit - a larger heap is
    just a larger heap, and a service reporting latency for every megabyte it
    allocates would make memory undiagnosable by making everything look like
    memory. Past that the collector is running more or less constantly, and the
    curve is linear to the limit because what it is competing for is the one
    thing the shop cannot get more of.
    """
    if pressure <= _PRESSURE_BEGINS_AT:
        return 1.0

    how_far_in = (pressure - _PRESSURE_BEGINS_AT) / (1.0 - _PRESSURE_BEGINS_AT)

    return 1.0 + how_far_in * (_SLOWEST_UNDER_PRESSURE - 1.0)


def _waiting_on_the_provider(share_of_minute_refused: float) -> float:
    """How much of this minute's latency was spent waiting on somebody else.

    Added to the shop's own time rather than multiplying it, because that is
    what waiting is: the request does its own work at the speed it always did
    and then sits on a socket until the timeout. A multiplier would make the
    wait proportional to how fast Io happens to be, which is the wrong way
    round - the provider's timeout is the provider's.

    Scaled by how much of the minute the provider was refusing, so the minute an
    outage starts in reads as part of one and not as the whole of it.
    """
    return _PROVIDER_TIMEOUT_MS * share_of_minute_refused


def _waiting_on_the_pricing_service(share_of_minute_slow: float) -> float:
    """How much of this minute's latency was spent waiting on the pricing
    service.

    Added rather than multiplying, for the reason waiting on the payment
    provider is added: the request does its own work at the speed it always did
    and then waits. The wait is the pricing service's, and scaling it by how fast
    Io happens to be would be the wrong way round.

    The whole of it is added to every quantile, because every account page shows
    a basket total - there is no cohort here and no fast path left over. That is
    the difference between this and the rollout, and it is why this incident is
    visible in the median.
    """
    return _PRICING_SLOW_CALL_MS * share_of_minute_slow


def _with_the_allocations_that_failed(failures: list[str], pressure: float) -> list[str]:
    """The minute's failures, plus the ones a shop at its limit cannot serve.

    Last of the three signals to move, which is the whole shape of a leak: the
    heap climbs for an hour, latency follows it for the back half of that, and
    the error rate only goes anywhere once allocations actually start failing.
    A responder paging on error rate alone finds out last.

    Topped up rather than replaced: the shop's ordinary failures are still
    happening, and a minute that reported only allocation failures would have
    lost the baseline noise every other minute has.
    """
    if pressure < _FAILING_BEGINS_AT:
        return failures

    failing = round(_FAILING_SHARE * _SAMPLE_SIZE)

    return [*failures, *([_OUT_OF_MEMORY_FAILURE] * max(0, failing - len(failures)))]


def _heap_lines_for(minute_id: str,
                    minute: datetime,
                    heap_bytes: int,
                    pressure: float,
                    lifetime: ProcessLifetime) -> tuple[str, ...]:
    """What the shop says about its own memory this minute.

    Quiet while there is nothing to say. A service that logged its heap every
    minute would bury the minute it mattered, and a reader scanning for the
    first mention of memory is doing what a responder does.

    A restart is two lines, because it is two events: the process that was
    serving went away, and another one came up. Said in the order they
    happened, with the new heap named - the reclaim is the thing a reader is
    checking for, and a restart that did not reclaim is a different incident.
    """
    restarted = [
        moment for moment in lifetime.restarts
        if moment.replace(second=0, microsecond=0) == minute
    ]
    said: list[str] = []

    if restarted:
        said.append(f"{minute_id} WARN io-shop: process terminated")
        said.append(
            f"{minute_id} INFO io-shop: process started - heap reclaimed to "
            f"{_as_mib(heap_bytes)}"
        )

    if pressure >= _FAILING_BEGINS_AT:
        said.append(
            f"{minute_id} ERROR io-shop: heap allocation failed - "
            f"{_as_mib(heap_bytes)} of {_as_mib(MEMORY_LIMIT_BYTES)} limit"
        )
    elif pressure > _PRESSURE_BEGINS_AT:
        said.append(
            f"{minute_id} WARN io-shop: heap at {_as_mib(heap_bytes)} of "
            f"{_as_mib(MEMORY_LIMIT_BYTES)} limit"
        )

    return tuple(said)


def _as_mib(memory_bytes: int) -> str:
    return f"{memory_bytes // _BYTES_PER_MIB}MiB"


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
    the failure's own words if it failed, and what the request cost.

    The flag value is carried out rather than discarded because it is what the
    request was actually decided by, and a service that routes on a flag logs
    the value it routed on.

    `latency_ms` is `None` for a shop with no cache configured, and that is not
    a missing measurement - it is this generator saying the request's cost was
    not composed from the path it took. Such a minute reports the baseline
    latency model the other scenarios use, because nothing about them turns on
    which requests were fast.

    `cache_failure` is carried for the same reason the flag value is: the shop
    said it, and a minute's log lines are assembled from what the shop said.
    `pricing_delay` is carried for that reason too, and it is the one thing in a
    minute of this scenario's telemetry that names where the time went.
    """

    flag_is_on: bool
    failure: str | None
    latency_ms: int | None = None
    from_cache: bool = False
    cache_failure: str | None = None
    pricing_delay: str | None = None


def _serve_one_account_page(
    entropy: random.Random,
    share_of_minute_flagged: float,
    share_of_minute_refused: float = 0.0,
    cache_entropy: random.Random | None = None,
    share_of_minute_without_the_cache: float = 0.0,
    cache_endpoint: CacheEndpoint | None = None,
    the_rollout_reached_it: bool = False,
    the_flag_ships_the_slow_feature: bool = False,
    the_flag_ships_the_statement: bool = False,
    statement_period: StatementPeriod | None = None,
    the_pricing_call_was_slow: bool = False,
) -> _ServedPage:
    """Puts one request through the shop and records how it went.

    The decisions above the call are the ones a real request would arrive with
    already made - which cohort the flag evaluated to for this shopper, whether
    they fall inside the rollout's canary share, and what the payment provider
    is doing at the moment the page asks it for their card. The shop itself is
    handed the answers, exactly as `io_shop.account_page` is handed them in
    production; nothing about the page's behaviour is simulated here.

    Neither the flag nor the provider is consulted per request - the two shares
    already carry how much of this minute each was in force for, so a minute the
    flag was on for half of routes half as much traffic to the canary as one it
    was on throughout. That is the whole mechanism by which a mid-minute revert
    shows up as a falling error rate, and it is why an outage that began
    mid-minute does not read as a step.

    The provider is only drawn for when there is an outage to draw from. A
    scenario that stages none takes no draw at all, which is what keeps every
    figure this generator produced before there was a provider exactly where it
    was: one generator is seeded per minute, and a draw inserted into the
    sequence shifts every draw after it.

    The cache draws from a generator of its own, seeded separately, rather than
    from the one above. That is a stronger arrangement than drawing
    conditionally: a separate sequence cannot disturb the shared one however
    many values it takes, so the cache can be consulted on every request of
    every scenario - which is what lets a hit ratio be reported everywhere -
    without moving a single figure this generator already produced.

    `the_rollout_reached_it` is decided by the caller rather than drawn here,
    and it is the one cohort decision in this module that is not a draw. Which
    requests the newest feature reached is an exact count of the minute's
    sample, for the reason written where that share is declared: at three in a
    hundred, a draw is wrong about which percentile the incident appears in
    often enough to cost the scenario the only thing it demonstrates.

    `the_flag_ships_the_slow_feature` and `the_flag_ships_the_statement` say
    which feature the flag is shipping, because the shop has one flag and
    several scenarios behind it. Most ship the monthly summary, whose divisor
    is empty for a shopper who has bought nothing this month; one ships the
    typical purchase, which is correct for everybody and merely slow; one ships
    the monthly statement panel, which breaks for exactly the shoppers the
    summary breaks for and does it in a far larger file.

    At most one of them is true at a time, and that is a property of the
    scenarios rather than something checked here. Shipping two at once would
    not be a second scenario, it would be one scenario staging two faults: the
    error rate would move, and the incident meant to be invisible in every
    aggregate but the tail would become visible in the first one anybody looks
    at.

    Parameters rather than inferences from `the_rollout_reached_it`, because
    that one is true only for the cohort and these have to hold for every
    request of the minute.
    """
    flag_is_on = entropy.random() < share_of_minute_flagged
    in_the_canary = flag_is_on and entropy.random() < CANARY_SHARE
    # One flag, and what it ships depends on which scenario is staged - see the
    # docstring above for why at most one of the alternatives is ever true.
    #
    # The canary is still drawn either way, so that suppressing the summary
    # here cannot shift a single figure in the scenarios that do ship it.
    use_monthly_summary = in_the_canary and not (
        the_flag_ships_the_slow_feature or the_flag_ships_the_statement
    )
    # The third thing the one flag can ship. Same cohort as the monthly summary
    # and the same shoppers break it - a month with nothing bought in it has no
    # largest purchase any more than it has an average - but the fault is in a
    # different file, and which file is the whole reason this scenario exists.
    # See `io_shop.monthly_statement`.
    use_monthly_statement = in_the_canary and the_flag_ships_the_statement
    provider_refused = (
        share_of_minute_refused > 0.0
        and entropy.random() < share_of_minute_refused
    )
    an_account_of_theirs = _an_account(entropy)

    page = serve_account_page(
        an_account_of_theirs,
        use_monthly_summary=use_monthly_summary,
        ask_the_provider=_the_provider_answering(refusing=provider_refused),
        ask_the_pricing_service=_the_pricing_service_answering(
            slowly=the_pricing_call_was_slow
        ),
        look_up_summary=_the_cache_answering(
            cache_entropy,
            share_of_minute_without_the_cache,
            could_hold_this_figure=not the_rollout_reached_it
        ),
        cache_endpoint=cache_endpoint,
        use_typical_spend=the_rollout_reached_it,
        use_monthly_statement=use_monthly_statement,
        statement_period=statement_period
    )
    cost = _what_the_page_cost(
        cache_entropy,
        page.served_from_cache,
        walked_once_per_item=the_rollout_reached_it,
        items_bought=len(an_account_of_theirs.purchases)
    )

    if page.failure is not None:
        return _ServedPage(flag_is_on, page.failure, cost, page.served_from_cache,
                           page.cache_failure)

    if entropy.random() < _BASELINE_ERROR_RATE + entropy.uniform(
        -_BASELINE_ERROR_RATE_WOBBLE, _BASELINE_ERROR_RATE_WOBBLE
    ):
        # Every real service fails a little without anything being wrong. Some
        # baseline noise is what makes "departed from baseline" a judgement
        # rather than a comparison against zero.
        #
        # A shopper who went away mid-response, and deliberately nothing that
        # names a dependency: the shop's ordinary noise is read in every window
        # of every scenario, and noise that named the payment provider would be
        # a standing accusation against a third party in incidents that have
        # nothing to do with one.
        return _ServedPage(
            flag_is_on, "ClientDisconnected: the shopper closed the connection",
            cost, page.served_from_cache, page.cache_failure, page.pricing_delay
        )

    return _ServedPage(flag_is_on, None, cost, page.served_from_cache,
                       page.cache_failure, page.pricing_delay)


def _the_cache_answering(cache_entropy: random.Random | None,
                         share_of_minute_without_the_cache: float,
                         could_hold_this_figure: bool = True) -> LookUpSummary | None:
    """The cache, as this request finds it.

    `None` where the deployment configured no cache at all, which is what every
    scenario staged before there was one looks like - the page then computes as
    it always did, and no draw is taken.

    Whether this request was served depends on two things: whether the shop
    could reach the cache at all this minute, and, if it could, whether this
    shopper's figure happened to be in it. Both are draws rather than
    decisions, because a hit ratio is a property of traffic rather than of any
    one request.

    `could_hold_this_figure` is false for a request the newest feature reached,
    and it is a miss by construction rather than by luck: what the cache holds
    is the figure the old rendering produced, and this request is asking for a
    different one. That is why a rollout costs what it costs - every page it
    reaches computes - and it is also why the hit ratio dips by the rollout's
    own share, which over two hundred sampled requests is inside the noise the
    ratio already has.

    Both draws are taken either way, so that a request skipping the cache
    cannot shift the sequence for the requests after it.
    """
    if cache_entropy is None:
        return None

    unreachable = cache_entropy.random() < share_of_minute_without_the_cache
    held_it = cache_entropy.random() < _HEALTHY_HIT_SHARE and could_hold_this_figure

    def look_up(shopper_id: str) -> CacheAnswer:
        if unreachable:
            return CacheAnswer(reached=False)

        return CacheAnswer(
            reached=True,
            summary_cents=_A_CACHED_FIGURE_CENTS if held_it else None
        )

    return look_up


def _what_the_page_cost(cache_entropy: random.Random | None,
                        served_from_cache: bool,
                        walked_once_per_item: bool = False,
                        items_bought: int = 0) -> int | None:
    """How long this request took, in milliseconds.

    `None` where there is no cache, because then the request's cost was never
    composed from the path it took - the minute reports the baseline latency
    model instead, exactly as it did before there was a cache.

    Where there is one, the cost follows the path: a cached page skips walking
    the shopper's purchase history, and that walk is the expensive part. The
    three paths are drawn around different centres and all wobble, so a minute's
    quantiles are real percentiles over a real mixture rather than three numbers
    somebody decided on.

    The third path is the one the newest feature takes, and it is the only one
    whose centre is not a constant: it collects the prices once and then walks
    what is left again per item, so it costs a recompute plus a recompute for
    every item bought. A shopper with one purchase already costs twice what an
    ordinary miss costs, and a shopper with a dozen waits more than two seconds
    - which is why the requests at the top of this minute's tail are the ones
    belonging to the best customers, and why none of these pages ever lands in
    the band a recomputed page occupies.

    One draw whichever path was taken, so that adding the third left the
    sequence exactly as long as it was.
    """
    if cache_entropy is None:
        return None

    if walked_once_per_item:
        # A recompute *and then* the repeated scans, because that is what the
        # code does: it walks the history once to collect the prices, and only
        # then starts taking the cheapest that is left. So the cheapest page
        # this path can serve - a shopper who has bought one thing - already
        # costs twice what a miss costs and never lands in the band a
        # recomputed page occupies.
        #
        # Faithfulness rather than arithmetic: it leaves the 95th percentile
        # where it was. That quantile moves from 189ms to 196ms across the onset
        # whatever this floor is, and the cause is structural rather than a
        # question of centres - the requests the rollout takes are no longer
        # cacheable, so the remaining traffic has fewer of them and the 190th
        # value sits higher up the same band of misses. Well inside the calm
        # range either way, and nowhere near a departure.
        centre = _RECOMPUTED_PAGE_MS + _RECOMPUTE_PER_ITEM_MS * items_bought
    elif served_from_cache:
        centre = _CACHED_PAGE_MS
    else:
        centre = _RECOMPUTED_PAGE_MS

    return max(
        1,
        centre + cache_entropy.randint(
            -_PAGE_LATENCY_WOBBLE_MS, _PAGE_LATENCY_WOBBLE_MS
        )
    )


def _the_provider_answering(refusing: bool) -> AskTheProvider:
    """The payment provider, as this request finds it.

    A function rather than a client, because the shop is rendered two hundred
    times a minute across a ninety-minute window and a socket per render would
    be tens of thousands of requests for one read of `/metrics`. What is real is
    the shape of the answer and what Io does with it - the status comes back
    here and the failure is composed in the shop, where a client's is.
    """
    def ask(shopper_id: str) -> ProviderAnswer:
        if refusing:
            return ProviderAnswer(status=_PROVIDER_IS_UNAVAILABLE)

        return ProviderAnswer(status=_PROVIDER_ANSWERED, card=_A_CARD)

    return ask


def _the_pricing_service_answering(slowly: bool) -> AskThePricingService:
    """The pricing service, as this request finds it.

    A function rather than a client for the reason the provider's is one, and
    with one difference that matters: the answer is always a price. A degraded
    pricing service is slow and correct, so there is no status to vary and
    nothing for the shop to fail on - what varies is the one number the shop
    turns into a log line.

    No draw is taken either way, which is what keeps every figure this generator
    already produced exactly where it was: one generator is seeded per minute,
    and a draw inserted into the sequence shifts every draw after it.
    """
    def ask(shopper_id: str) -> PricingAnswer:
        return PricingAnswer(
            total_cents=_A_BASKET_TOTAL_CENTS,
            took_ms=_PRICING_SLOW_CALL_MS if slowly else _PRICING_CALL_MS
        )

    return ask


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
        # Named from the history itself, because there is no shopper database
        # here to draw a name out of - and named without drawing anything, so
        # that adding an identity left every figure this generator already
        # produced exactly where it was.
        shopper_id=f"shopper-{sum(prices)}-{len(prices)}-{purchases_this_month}",
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
    flag: str | None,
    decoy: str | None = None,
    heap: tuple[str, ...] = (),
    cache: tuple[str, ...] = (),
    pricing: tuple[str, ...] = (),
) -> tuple[str, ...]:
    evaluations = (
        *(
            (_flag_evaluation_line(minute_id, sample_size, evaluated_on, flag),)
            if flag is not None
            else ()
        ),
        *((decoy,) if decoy is not None else ()),
        *heap,
        *cache,
        *pricing,
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
    # Two decimals, always - and the width fixed at the render site rather than
    # left to the value. To the nearest whole percent a single failure in 200 is
    # 0.5% and rounds to 0, which would put "error rate at 0%" directly beneath
    # the ERROR line that failure produced; this is the line a reader scans and
    # the line the model is handed, and the two must not disagree. Rounding up
    # instead would fix the zero by lying the other way - 1 in 2000 is not 1% -
    # and the model reads this figure as the incident's severity.
    rate_percent = 100 * len(failures) / sample_size
    aggregate = (
        f"{minute_id} WARN io-shop: account page error rate at {rate_percent:.2f}% "
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
