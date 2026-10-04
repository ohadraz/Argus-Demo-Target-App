"""The alerting half of the shop's monitoring.

A real service does not tell anyone it is unwell. Something watches it - a
metrics stack with alert rules - and *that* is what posts a webhook when a rule
fires. So the alert is sent from here, one server reaching another, rather than
from the console page in someone's browser. Nothing about a Grafana webhook is
browser-shaped: it has no origin, no preflight, and it fires whether or not a
human has the page open.

Kept in its own module for the same reason it used to live in the page: the
shop's own request path carries no reference to the tool watching it. This is
the monitoring stack, hosted inside the fixture for convenience, not a feature
of the shop.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import httpx2
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from io_shop.cache_reconciliation import CacheReconciliation
from io_shop.spend_reconciliation import Reconciliation
from target_app.integrity import THE_CHECK_RUNS_EVERY
from target_app.scenarios import (
    AUTOSCALER_FLAPPING,
    BAD_DEPLOYMENT,
    CACHE_MISCONFIGURED,
    CPU_SATURATION,
    PRICING_SERVICE_DEGRADED,
    RESOURCE_LEAK,
    SLOW_CANARY_ROLLOUT,
    TIMESTAMP_FORMAT,
    utc_now,
)

HttpPost = Callable[..., httpx2.Response]

# The receiver runs the whole incident - investigation, mitigation, and the
# verification wait after the action - before it answers, so this waits far
# longer than an HTTP call normally should. A real alerting stack would fire and
# forget; this one holds on because the console has nothing else to report the
# incident id from.
REQUEST_TIMEOUT_SECONDS = 300.0

# The service these alerts are about, as its logs and metrics name it.
SERVICE_NAME = "io-shop"

# Which rule fired. A deploy that slowed the shop down trips a different rule
# than one that made it throw, and the alert name is the only part of the
# payload that says which.
_HIGH_LATENCY = "HighLatency"
_HIGH_ERROR_RATE = "HighErrorRate"
_HIGH_MEMORY_USAGE = "HighMemoryUsage"
# The one rule here that is not written against a series at all. Every name above
# names something a metrics stack measures over time; this one names a comparison
# between two stored figures, which is the only way the incident behind it can be
# reported - no series moves, so no threshold is ever crossed.
_TOTALS_DO_NOT_RECONCILE = "SpendTotalsDoNotReconcile"
# The second rule here not written against a value, and the only one written
# against the *absence* of one. Every name above fires because a series crossed
# a line; this one fires because a series that was reporting stopped, which no
# threshold can express - there is nothing to compare. Every real monitoring
# stack has this rule and it is the last shape of alert this shop could not
# raise.
_METRICS_ABSENT = "MetricsAbsent"
# The third rule here not written against a value, and the one that fires on a
# disagreement between two copies rather than on either copy alone. Named for the
# cached figures rather than for the totals, because `SpendTotalsDoNotReconcile`
# above is the same comparison made against the authoritative copy and the two
# must never be confused: there the stored figure is wrong and rewriting data is
# the only repair, here the stored figure is right and the copy in front of it is
# stale. A responder who reads one name for the other reaches for the wrong one
# of those.
_CACHED_TOTALS_ARE_STALE = "CachedSpendTotalsAreStale"

# How long the shop has to go unheard-from before that is an incident rather
# than a gap.
#
# Not one minute. A scrape that did not land is an ordinary event in every
# monitoring stack there is, and a rule that paged on one would page most days -
# so the dwell is what separates an absence from a miss, exactly as a duration
# separates a spike from a departure in the rules above. Two minutes is long
# enough that nothing routine reaches it and short enough that the shop is not
# unwatched for a quarter of an hour before anybody hears.
_LONG_ENOUGH_TO_BE_AN_ABSENCE = timedelta(minutes=2)

# How many disagreeing totals the shop pages somebody for.
#
# Not one. A single total that does not add up is a support ticket and a
# correction, and a rule that fired on it would fire most weeks; what is worth
# waking somebody for is a *population* of them, because that is what says a write
# path rather than a row is wrong. Twenty-five is comfortably above the noise a
# real shop lives with and far below what this scenario stages.
_ENOUGH_TOTALS_TO_PAGE = 25

# And how many stale cached figures the shop pages somebody for.
#
# Its own figure rather than the one above reused, because the two are findings
# about different things and a shop could reasonably tolerate different amounts
# of each. The same reasoning sets it: one entry out of step is a cache doing
# what caches do between a write and an invalidation, and what is worth waking
# somebody for is a population of them - which is what says the thing feeding the
# cache is wrong rather than one entry being briefly behind.
_ENOUGH_STALE_ENTRIES_TO_PAGE = 25

# Which annotation carries the minute the writing went wrong.
#
# Its own annotation and not a sentence to be parsed out of the summary, because
# it is the one figure in this payload a consumer has to do arithmetic with: it is
# the onset, and the alert is the only thing in the whole incident that knows it.
# Prose in the summary too, for a reader - but a reader and a consumer should not
# be sharing one field when one of them needs a timestamp.
_THE_ONSET_ANNOTATION = "onset"
_THE_SUMMARY_ANNOTATION = "summary"

# Which annotation carries the addresses of the entries found stale, and which
# carries the instant they stopped keeping up.
#
# The addresses are here because the job had to address the cache to compare it,
# so it already holds them, and because how an entry is addressed is the shop's
# own business - a consumer that composed one from a shopper id would be
# guessing at a format nobody published to it. Comma-separated in one annotation
# rather than one annotation each: an alert envelope carries a flat map of
# strings, and a consumer reading `entry.1`, `entry.2` would be parsing a list
# out of key names.
#
# They are also the one field here that is acted on rather than read. Everything
# else in this payload describes the incident; this says exactly which entries
# are wrong, and anything acting on fewer or more than these is acting beyond
# what the check established.
_THE_STALE_ENTRIES_ANNOTATION = "stale_entry_keys"
_THE_STALE_COUNT_ANNOTATION = "stale_entries_found"
# And when the copies stopped keeping up, which is not the onset. The onset is
# when shoppers began reading stale figures; this is when the figures froze, and
# it is earlier. Carried separately because a reader given one of them cannot
# derive the other, and because the gap between them is itself the finding - a
# copy that had been behind for hours before anybody was served from it.
_THE_DIVERGENCE_BEGAN_ANNOTATION = "divergence_began"

# Which annotation a rule says what it looked at in, and what the three rules
# here that looked at something other than a series say.
#
# Set by those three alone. A threshold rule says nothing, which is what a
# threshold rule in any real stack does - and a consumer reading silence has to
# take it for the common case, so there is nothing for one to add. What these
# three know and no series carries is that their subject was never a series: the
# spend check compared stored totals against the records behind them, the cache
# check compared two copies, and the absence rule noticed there were no figures
# at all. A window holding no departure contradicts none of the three, and only
# the rule is in a position to say so.
_THE_CLAIM_ANNOTATION = "claim"
_A_FINDING_OF_THE_RULES_OWN = "own-finding"

# What a hundredth of the shop's currency is called when a figure is said in
# whole units. The gap is carried in cents everywhere else, because that is what a
# price is stored in; an alert is read by a person, and a person reads money.
_CENTS_IN_A_UNIT = 100

# The rule each scenario trips, and what it says. Anything not named here is
# paging about an error rate, which is what most of these scenarios break.
#
# The leak's rule is the one worth arguing about: it pages on memory, because
# by the time a leak moves the error rate the shop has been failing for a while
# and the alert is late. Its summary says so - the responder is being told
# about a climb, not an outage.
_WHAT_FIRED: dict[str, tuple[str, str]] = {
    BAD_DEPLOYMENT: (_HIGH_LATENCY, "p95 latency above threshold for 5m"),
    # The median rather than the tail, and that is the scenario rather than a
    # detail: nine requests in ten were served from cache, so the tail always
    # described a recomputed page and barely moves when the cache goes. A rule
    # written against p95 - which is how most latency alerting is written -
    # would never fire on this at all.
    CACHE_MISCONFIGURED: (
        _HIGH_LATENCY, "p50 latency above threshold for 5m"
    ),
    # The other percentile most latency alerting is not written against, and
    # the other half of the same lesson. Three requests in a hundred is below
    # the 95th by arithmetic and fails nothing, so a rule on p95 never fires
    # here and a rule on the error rate never fires either. Saying which
    # percentile moved is most of what the responder is being told.
    SLOW_CANARY_ROLLOUT: (
        _HIGH_LATENCY, "p99 latency above threshold for 5m"
    ),
    RESOURCE_LEAK: (
        _HIGH_MEMORY_USAGE,
        "Memory usage climbing against the container limit for 15m",
    ),
    # The same rule the bad deployment trips, and deliberately the same words:
    # every request got slower in both, so the p95 rule is the one that fires
    # and there is nothing in a firing alert that could distinguish them. The
    # alert names the shop, because the shop is what is being paged about - and
    # the shop is not what is wrong, which is the whole incident.
    PRICING_SERVICE_DEGRADED: (
        _HIGH_LATENCY, "p95 latency above threshold for 5m"
    ),
    # A third scenario on the same rule and the same words, which by now is the
    # point rather than a coincidence: a bad deployment, a slow neighbour and a
    # shop with more traffic than it has cores are one alert, and separating them
    # is the whole of the investigation. Nothing here pages on utilisation - a
    # rule that fired whenever CPU rose would fire every evening, and the thing
    # worth waking somebody for is the latency it caused.
    CPU_SATURATION: (
        _HIGH_LATENCY, "p95 latency above threshold for 5m"
    ),
    # A fourth, and the same words again. The temptation here is to page on the
    # replica count moving, which is the one signal that would name this incident
    # from the alert alone - and it would be a rule no real monitoring stack has,
    # because a deployment resizing is what an autoscaler is *for*. What is worth
    # waking somebody for is that the latency never settles; that the capacity is
    # what will not settle is the investigation's to find, and it is retrievable.
    AUTOSCALER_FLAPPING: (
        _HIGH_LATENCY, "p95 latency above threshold for 5m"
    ),
}
_BY_DEFAULT = (_HIGH_ERROR_RATE, "Error rate above threshold for 5m")


class MonitoringSettings(BaseSettings):
    """Where this service's monitoring sends the alerts it raises.

    The default is Argus on the host, which is where it runs in the documented
    setup. From inside the container the host is not `localhost`, so
    `docker-compose.yml` overrides this.
    """

    model_config = SettingsConfigDict(env_prefix="MONITORING_", extra="ignore")

    alert_webhook_url: str = Field(default="http://localhost:8000/webhooks/alerts")


@lru_cache
def get_monitoring_settings() -> MonitoringSettings:
    return MonitoringSettings()


class AlertNotDelivered(Exception):
    """The alert could not be handed to whatever is listening for it.

    Raised rather than swallowed, so the console can say the scenario is staged
    but nobody was told - the incident is real either way, and a staging that
    silently alerted nobody looks exactly like one that did.
    """


AddressOf = Callable[[str], str]


def an_alert_for(scenario_id: str | None,
                 at: datetime,
                 finding: Reconciliation | None = None,
                 unheard_from_since: datetime | None = None,
                 stale: CacheReconciliation | None = None,
                 promoted_at: datetime | None = None,
                 address_of: AddressOf = str) -> dict[str, Any]:
    """The firing alert, in Grafana Alertmanager's own webhook shape.

    Deliberately the vendor's shape, field nesting included: the consumer parses
    this with the same code it would point at a real Grafana, so a friendlier
    payload here would be a lie that consumer would have to be written around.

    `finding` is what the shop's data-integrity job last reported, where anything
    has asked it. It takes precedence over the map below when it carries enough
    disagreeing totals to page about, because it is the only thing here that knows
    something no series does - and it is ignored entirely otherwise, which is what
    keeps a quiet shop from being paged about a check that found nothing.

    `unheard_from_since` is the first minute the shop published nothing, where it
    has stopped publishing at all. It is the second thing here that knows what no
    series does, and for the opposite reason: the check above reads figures no
    rule watches, and this one is the rule noticing there are no figures. Like the
    finding, it is ignored entirely when absent, which is what keeps a shop that
    is reporting from being paged about silence.

    `stale` is what the same job found when it asked the cache the same question,
    and `address_of` spells a shopper's entry the way the cache stores it. The
    spelling is handed in rather than composed here: this module reports what was
    found, and how an entry is addressed belongs to whatever keeps the cache. The
    default spells a shopper as itself, which is what a shop with no cache in
    front of its totals would say.

    `promoted_at` is when the stale copy was put in front of shoppers, and it is
    the onset of that incident. The finding's own oldest missing purchase is
    earlier and is not the onset - it is when the copy stopped keeping up, which
    is a fact about the copy rather than about the incident.

    It is checked against the dwell rather than taken on trust. A rule of this
    kind that fired the moment a sample was late would fire most days, so an
    absence younger than `_LONG_ENOUGH_TO_BE_AN_ABSENCE` is a miss and not an
    incident - and what fires then is whatever the shop's state warrants, which
    for a shop that is otherwise well is nothing worth reading. Saying so in the
    rule rather than at the caller is deliberate: how long silence has to last is
    the monitoring stack's judgement, exactly as a threshold's duration is.
    """
    if finding is not None and len(finding.accounts_that_disagree) >= (
        _ENOUGH_TOTALS_TO_PAGE
    ):
        return _one_firing_alert(
            _TOTALS_DO_NOT_RECONCILE, _what_the_check_found_said(finding), at,
            onset=finding.oldest_affected_purchase_at,
            reports_its_own_finding=True
        )

    if stale is not None and len(stale.entries_that_disagree) >= (
        _ENOUGH_STALE_ENTRIES_TO_PAGE
    ):
        return _one_firing_alert(
            _CACHED_TOTALS_ARE_STALE,
            _what_the_cache_check_found_said(stale, promoted_at),
            at,
            # The promotion, which is when shoppers began reading stale figures.
            # Deliberately not the oldest missing purchase, which is earlier and
            # is when the copies stopped keeping up - that goes in its own
            # annotation below. An onset is when the incident began, and nobody
            # was served a wrong figure until the promotion.
            onset=promoted_at,
            reports_its_own_finding=True,
            also={
                _THE_STALE_ENTRIES_ANNOTATION: ",".join(
                    address_of(entry.shopper_id)
                    for entry in stale.entries_that_disagree
                ),
                _THE_STALE_COUNT_ANNOTATION: str(len(stale.entries_that_disagree)),
                **(
                    {
                        _THE_DIVERGENCE_BEGAN_ANNOTATION:
                            stale.oldest_missing_purchase_at.strftime(TIMESTAMP_FORMAT)
                    }
                    if stale.oldest_missing_purchase_at is not None
                    else {}
                ),
            }
        )

    if (
        unheard_from_since is not None
        and at - unheard_from_since >= _LONG_ENOUGH_TO_BE_AN_ABSENCE
    ):
        return _one_firing_alert(
            _METRICS_ABSENT,
            _what_the_silence_said(unheard_from_since, at),
            at,
            onset=unheard_from_since,
            reports_its_own_finding=True
        )

    alertname, summary = _WHAT_FIRED.get(scenario_id or "", _BY_DEFAULT)

    return _one_firing_alert(alertname, summary, at)


def _what_the_silence_said(unheard_from_since: datetime, at: datetime) -> str:
    """The absence in the words a responder is paged with.

    Two figures, and the second is what makes this alert unlike every other one
    here. When the last sample arrived says where to look; *how long ago that
    was* says this is an absence rather than a gap, and it is the only thing in
    the payload that distinguishes the two.

    It also says what the alert is not about, in as many words. A responder
    reading that a shop has stopped reporting will reach first for the shop, and
    the one thing this rule can be sure of is that the shop answered the request
    that would have carried these figures - because nothing here is a health
    check. What is unknown is everything the figures would have said, and a
    summary that left that implicit invites the reading it exists to prevent.
    """
    minutes = int((at - unheard_from_since).total_seconds() // 60)

    return (
        f"No metrics received from io-shop since "
        f"{unheard_from_since.strftime(TIMESTAMP_FORMAT)} - {minutes}m with no "
        f"sample, against a scrape interval of one minute. The series stopped "
        f"rather than crossing a threshold, so nothing here says whether the "
        f"shop is well: these minutes have no readings to judge, not readings "
        f"that look bad."
    )


def _one_firing_alert(alertname: str,
                      summary: str,
                      at: datetime,
                      onset: datetime | None = None,
                      reports_its_own_finding: bool = False,
                      also: Mapping[str, str] | None = None) -> dict[str, Any]:
    """One firing alert in the vendor's envelope, with whatever it can say.

    `startsAt` is when the rule fired, always, for every alert this shop sends.
    For a rule watching a series that is also roughly when the service departed
    its baseline; for the integrity check it is a week later than the fault, which
    is exactly why the onset is carried separately and not inferred from this.

    `reports_its_own_finding` is how a rule says its subject was never a series.
    False by default, because a threshold rule's subject is a series and a rule
    with nothing unusual to say about itself should say nothing - an annotation
    on every alert this shop sends would be a field a consumer has to read to
    learn the ordinary case.
    """
    annotations = {_THE_SUMMARY_ANNOTATION: summary}

    if onset is not None:
        # Absent rather than empty where the check could not date its own
        # finding. A consumer reading an onset it can parse and act on must not
        # also have to decide whether a blank one means "now".
        annotations[_THE_ONSET_ANNOTATION] = onset.strftime(TIMESTAMP_FORMAT)

    if reports_its_own_finding:
        # Absent rather than spelled the other way for the ordinary rule, which
        # is the same judgement the onset above is given. A consumer defaulting a
        # silence is a consumer that goes on working when a rule nobody here
        # wrote fires, and every such rule is watching a series.
        annotations[_THE_CLAIM_ANNOTATION] = _A_FINDING_OF_THE_RULES_OWN

    # Whatever else this particular rule has to say that a consumer acts on
    # rather than reads. Absent for every rule that has nothing of the kind,
    # which is all of them but one.
    annotations.update(also or {})

    return {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "service": SERVICE_NAME,
                    "severity": "critical",
                },
                "annotations": annotations,
                "startsAt": at.strftime(TIMESTAMP_FORMAT),
            }
        ],
    }


def _what_the_check_found_said(finding: Reconciliation) -> str:
    """The finding in the words a responder is paged with.

    Three figures, and the third is the one that makes this alert unlike every
    other one here. How many totals disagree says how big the incident is; the
    widest gap says how bad the worst of it is; and the oldest affected purchase
    says *when the writing went wrong*, which nothing else in the incident knows -
    the check runs weekly, so the minute this fired says nothing about it.

    Money in whole units to two places, because a person reads this. The cents
    stay cents in the annotation a consumer reads.
    """
    oldest = finding.oldest_affected_purchase_at
    since = (
        f"the oldest affected purchase was recorded at "
        f"{oldest.strftime(TIMESTAMP_FORMAT)}"
        if oldest is not None
        else "no purchase in the histories accounts for the gaps, so the finding "
             "cannot be dated"
    )

    return (
        f"{len(finding.accounts_that_disagree)} of {finding.accounts_checked} "
        f"shopper totals do not reconcile with the purchases behind them. The "
        f"widest is short by "
        f"{finding.largest_gap_cents / _CENTS_IN_A_UNIT:,.2f}, and {since}. "
        f"Found by the spend-integrity check, which runs every "
        f"{THE_CHECK_RUNS_EVERY.days} days, so the fault is up to that old."
    )


def _what_the_cache_check_found_said(stale: CacheReconciliation,
                                     promoted_at: datetime | None) -> str:
    """The cache finding in the words a responder is paged with.

    Says which copy is wrong, in as many words, because that is the whole of what
    separates this from the alert beside it and the whole of what decides the
    response. A responder who reads "totals do not reconcile" and acts on it
    repairs data; one who reads this discards a copy and leaves the data alone.

    Two instants, said as two, with the gap between them spelled out. A reader
    given only the onset would think the figures started being wrong when
    shoppers started seeing them, and would look for a change at that minute -
    where the thing that actually went wrong happened hours earlier and left no
    mark on any series.

    Money in whole units to two places, as the check beside it does, because a
    person reads this. The cents stay cents in the annotations a consumer reads.
    """
    missing_since = stale.oldest_missing_purchase_at
    behind = (
        f"the oldest purchase none of them account for was recorded at "
        f"{missing_since.strftime(TIMESTAMP_FORMAT)}, which is when the copies "
        f"stopped keeping up"
        if missing_since is not None
        else "nothing in the purchase histories says when the copies stopped "
             "keeping up"
    )
    served = (
        f", and they were being served from {promoted_at.strftime(TIMESTAMP_FORMAT)}"
        if promoted_at is not None
        else ""
    )

    most = stale.largest_items_short
    purchases = "purchase" if most == 1 else "purchases"

    return (
        f"{len(stale.entries_that_disagree)} of {stale.entries_checked} cached "
        f"spend figures disagree with the purchases behind them. The purchases "
        f"are correct and the cached copies are behind: the widest gap is "
        f"{stale.largest_gap_cents / _CENTS_IN_A_UNIT:,.2f}, the most any one "
        f"figure is missing is {most} {purchases}, and {behind}{served}. "
        f"Discarding the entries listed will send those pages back to the "
        f"purchase ledger, which never moved. The list is what disagreed when "
        f"this check ran: nothing is putting the copies right, so a figure goes "
        f"stale as soon as its shopper buys again and a later check will name "
        f"more."
    )


def fire_alert(
    scenario_id: str | None,
    settings: MonitoringSettings | None = None,
    post: HttpPost = httpx2.post,
    now: Callable[[], datetime] = utc_now,
    finding: Reconciliation | None = None,
    unheard_from_since: datetime | None = None,
    stale: CacheReconciliation | None = None,
    promoted_at: datetime | None = None,
    address_of: AddressOf = str,
) -> dict[str, Any]:
    """Posts the firing alert to the configured webhook and returns what came
    back, so a caller can report the incident it started.

    Every finding `an_alert_for` can build a payload from is a parameter here,
    because this is the only way anything outside the process reaches it: the
    check runs in-process and has no endpoint of its own, so a finding this
    signature cannot carry is a finding nothing can ever page about.

    A non-2xx answer is a failure to deliver, not a delivered alert: something
    is listening at that URL but did not accept the alert, and reporting that as
    raised would leave an incident nobody is handling looking handled.
    """
    resolved = settings if settings is not None else get_monitoring_settings()
    url = resolved.alert_webhook_url

    try:
        response = post(
            url,
            json=an_alert_for(
                scenario_id, now(), finding, unheard_from_since, stale,
                promoted_at, address_of
            ),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
    except Exception as error:
        raise AlertNotDelivered(f"could not raise an alert at [{url}]: {error}") from error

    return body
