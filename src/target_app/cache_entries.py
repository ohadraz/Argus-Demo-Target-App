"""The summary cache as a real store, for the one scenario about what it holds.

Every other scenario's cache is arithmetic: `_the_cache_answering` draws whether
the cache was reachable this minute and whether it happened to hold this
shopper's figure, and hands back a seam that answers from those two draws. That
is enough for a scenario about *latency* - a cache that is slow, or missing, or
pointed at the wrong port - because none of those care what any entry says.

One scenario does care. A replica that was behind gets promoted, so the cache
serves figures the purchase ledger has moved past, and the fault is in the
*contents*. Arithmetic cannot stage that: there has to be something holding a
wrong value, addressable by the key the shop would really use, so that
discarding the entry is a thing that can actually be done and counted.

So this module is the real client, and it is reached on that one staged path.
Nothing here changes what any other scenario draws - see
`generator._the_cache_answering`, whose two draws stay unconditional precisely
so that a request which skips the cache cannot shift the sequence for the
requests after it.

Nothing may widen that path to another scenario without re-recording it, and the
reason is sharper than tidiness. `cache-misconfigured` stages a shop dialling the
port the deployment names, which is one digit from the port that answers. While
the cache is arithmetic, that scenario's unreachability is a draw against a share
of the minute. Point it at the real store and the refusal becomes genuine - a
connection to a port nothing listens on - and the window it produces stops being
the window its recordings were made from. The upgrade looks like an improvement
and is a silent invalidation of a frozen corpus.

Why the key format lives here rather than in `io_shop.summary_cache`, beside the
endpoint and entry formats it plainly belongs with: three recorded fixes in the
grading corpus replace `summary_cache.py` wholesale. A constant added there would
vanish whenever one of those patches is applied, and anything importing it would
fail during grading rather than during a run - a failure pointing at the fix
corpus for a change that had nothing to do with it. `target_app` is the staging
harness and no patch has ever touched it, so the spelling is safe here.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import redis

from io_shop.summary_cache import CacheAnswer, CacheEndpoint, LookUpSummary, SummaryEntry
from target_app.settings import the_working_cache_endpoint

# How an entry is addressed. Colon-separated under the service's own name, which
# is the convention every Redis deployment uses to keep one store's keys apart
# from another's.
#
# No period in the key, though the figure it holds is a month's. A key carrying
# `2026-10` would be a key that changes meaning at midnight on the first, and
# the staged entries - written once, read back by their exact spelling, and
# discarded by a list of those spellings travelling through an alert - would
# stop matching mid-run for a reason visible in neither repository. The shop
# keeps the current month's figure under the shopper and lets it be replaced,
# which is what a cache with a TTL does and what the lasting fix for this
# incident would configure.
_KEY_FORMAT = "io-shop:summary:{shopper_id}"

# How long a request will wait on the cache before giving up on it. Named rather
# than inline because it is the number that decides whether losing the cache is
# a slowdown or an outage, which is the distinction the whole module rests on.
_SECONDS_TO_FIND_OUT_THE_CACHE_IS_GONE = 0.25


def the_key_for(shopper_id: str) -> str:
    """How the shop addresses one shopper's cached figure.

    One spelling, used to write an entry, to read it back, and to name it in the
    alert that reports it stale. Three places deriving it separately would
    eventually disagree about one character, and the symptom would be a discard
    that removed nothing while reporting success - which is precisely the
    outcome the receipt is supposed to make impossible.
    """
    return _KEY_FORMAT.format(shopper_id=shopper_id)


@contextmanager
def a_client_for(endpoint: CacheEndpoint) -> Generator[redis.Redis]:
    """A connection to the cache at `endpoint`, closed when the caller is done.

    `decode_responses` so what comes back is the text the shop wrote, which is
    what `io_shop.summary_cache` expects to be handed: reading an entry is that
    module's job, and a client returning bytes would be deciding here what an
    entry is.
    """
    client = redis.Redis.from_url(
        str(endpoint),
        decode_responses=True,
        # How long the shop waits to find out the cache is gone, and it has to
        # be short. Reaching an unreachable cache is a normal day here - the
        # page recomputes and renders - but only if discovering that is quick:
        # a client that spent seconds retrying would turn losing the cache from
        # the slowdown it is into a timeout, and the account page would fail for
        # a reason the cache's own design says it must not.
        #
        # The retry is off for the same reason. There is nothing to retry: a
        # refused connection is an answer, and asking again just spends the
        # page's budget to be told the same thing.
        socket_connect_timeout=_SECONDS_TO_FIND_OUT_THE_CACHE_IS_GONE,
        socket_timeout=_SECONDS_TO_FIND_OUT_THE_CACHE_IS_GONE,
        retry_on_timeout=False
    )

    try:
        yield client
    finally:
        client.close()


def looking_up_in(client: redis.Redis) -> LookUpSummary:
    """The seam `cached_summary` takes, answered by a real store.

    Built once around a client rather than per request, for the reason the seam
    exists at all: a minute of telemetry renders the shop many times over, and a
    connection per render would be thousands of them.

    A store that cannot be reached is reported as not reached rather than as
    holding nothing. The two end the same way - the page works the figure out
    for itself - and they mean opposite things to whoever is reading the shop's
    behaviour afterwards, which is why `CacheAnswer` carries them separately.
    """
    def look_up(shopper_id: str) -> CacheAnswer:
        try:
            written = client.get(the_key_for(shopper_id))
        except redis.RedisError:
            return CacheAnswer(reached=False)

        return CacheAnswer(reached=True, entry=written)

    return look_up


def write_entries(client: redis.Redis, entries: dict[str, SummaryEntry]) -> None:
    """Put a staged figure under each shopper named, as the shop would have.

    Written through `SummaryEntry.__str__`, which is the shop's own writer, so
    that a staged entry is indistinguishable from one the shop wrote itself. An
    entry spelled here would be this module's idea of the format, and the
    scenario would then be staging a cache the shop cannot read rather than a
    cache holding the wrong figure.

    One pipeline rather than a write per shopper: the staging writes every entry
    the scenario needs in one go, and a round trip each would be hundreds of
    them before the first request arrives.
    """
    pipeline = client.pipeline()

    for shopper_id, entry in entries.items():
        pipeline.set(the_key_for(shopper_id), str(entry))

    pipeline.execute()


def forget_every_cached_summary(endpoint: CacheEndpoint | None = None) -> None:
    """Empty the cache of every entry the shop ever wrote, wherever it answers.

    What a reset calls, and the reason it defaults its endpoint rather than being
    handed one: a reset runs with no scenario staged and therefore with no
    endpoint recorded, and the one address that ever answers is the working one -
    the deployed port is a staged mistake and nothing listens there.

    Handed one by a test, for the reason the values file is handed in: the
    deployment's address is a cluster service name that resolves inside the
    environment and nowhere else, so a suite running beside it has no way to
    reach the default and would be left asserting that an unreachable cache is
    tolerated - which is the one path that needs no proof.

    A cache that cannot be reached is left alone rather than retried or
    reported. The shop treats an unreachable cache as an ordinary day, every
    page is still correct without one, and a store that never answered is
    holding nothing a reset could clear. The failure this swallows is therefore
    the absence of the thing being cleared, which is the state being asked for.
    """
    try:
        with a_client_for(endpoint or the_working_cache_endpoint()) as client:
            discard_every_entry(client)
    except redis.RedisError:
        return


def discard_every_entry(client: redis.Redis) -> None:
    """Empty the cache of everything this module ever wrote.

    For the staging rather than for the incident: a scenario is staged into a
    store that may still hold the last one's entries, and a figure nobody staged
    is a figure the integrity check reports as an incident of its own. The
    compose service keeps no volume for the same reason, which makes this the
    guard for a restage within a single run rather than across runs.

    Scoped to this module's own keyspace by the one prefix it writes under, so a
    store shared with anything else keeps whatever belongs to the other party.
    """
    keys = list(client.scan_iter(match=the_key_for("*")))

    if keys:
        client.unlink(*keys)
