from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from io_shop.summary_cache import CacheEndpoint

# The deployment's own configuration, as it is mounted into the pod. Beside
# `src/` rather than inside it, because it is not source: it ships with the
# deployment, it is owned by whoever runs the shop, and it changes without the
# service being rebuilt.
VALUES_FILE = Path(__file__).resolve().parents[2] / "deploy" / "values-production.yaml"

# Where in that file the cache's address is written. Spelled out so that a
# reader comparing the shop's behaviour against the file knows which two lines
# decide it.
_CACHE = "cache"
_HOST = "host"
_PORT = "port"

# Where the cache actually listens, which is where the revision *before* the
# current one pointed the shop. A constant rather than a second read, because
# this service cannot read git: the previous revision's values live in the
# repository's history and the only thing here that knows them is this line.
# It is what a rollback puts back, and it is the port the shop runs on until a
# scenario applies the deployed configuration on top.
LAST_KNOWN_GOOD_CACHE_PORT = 6379


class UnleashSettings(BaseSettings):
    """Where the feature-flag provider is, and what to call it with.

    Defaults describe the provider as `docker-compose.yml` brings it up, so a
    developer running the service outside the stack still gets a working
    configuration by pointing `UNLEASH_BASE_URL` at a local Unleash and
    changing nothing else.

    Two tokens rather than one, because they are different powers. The frontend
    token can only evaluate; the admin token can change flag state, and is the
    one this service holds because it plays the operator who toggled the flag
    on.
    """

    model_config = SettingsConfigDict(env_prefix="UNLEASH_", extra="ignore")

    base_url: str = Field(default="http://localhost:4242")
    project: str = Field(default="default")
    # Production, because this service stands in for one - an incident staged
    # in a development environment would be asking an audience to imagine the
    # part that makes it an incident. Unleash's other built-in environments are
    # removed from the project at startup, so the flag carries exactly one
    # toggle and there is no wrong one to revert.
    environment: str = Field(default="production")
    flag: str = Field(default="monthly-spend-feature")
    # The other direction's flag. This one guards the *safe* path, so the shop
    # is healthy while it is on and breaks when somebody switches it off - a
    # kill switch withdrawn, which is as ordinary a cause of an incident as a
    # new feature switched on. A separate flag rather than the same one read
    # backwards, because one flag cannot honestly be both a new feature and the
    # fallback that protects against it.
    fallback_flag: str = Field(default="legacy-checkout-fallback")
    admin_token: str = Field(default="*:*.argus-demo-admin-token")
    frontend_token: str = Field(default="default:production.argus-demo-frontend-token")
    # The provider's own database, which a reset clears the flag history in -
    # see `target_app.history` for why that is not done through the API. The
    # default is the port `docker-compose.yml` publishes it on, so a service
    # run outside the stack against a local provider needs nothing set.
    database_url: str = Field(
        default="postgresql://unleash:unleash@localhost:5433/unleash"
    )


class ScenarioSettings(BaseSettings):
    """How a seeded scenario is staged.

    `onset_backdate_minutes` is why a scenario is diagnosable the instant it is
    seeded. Seeding turns the flag on now but records the incident as having
    begun this many minutes ago, so there is already enough history to locate an
    onset in - rather than a flat graph and a five-minute wait in front of an
    audience.

    Five rather than two or three: an investigation's initial log window reaches
    ten minutes past the onset it locates, and onset detection wants more than a
    single departed minute before it calls one.
    """

    model_config = SettingsConfigDict(env_prefix="SCENARIO_", extra="ignore")

    onset_backdate_minutes: int = Field(default=5, gt=0)

    # How long the shop keeps generating after the flag goes off, before the
    # scenario is done and its window stops advancing.
    #
    # It exists because recovery has to be *shown*, not just reached: a couple
    # of clean minutes after the drop are what turn "the number went down" into
    # "the number went down and stayed down". And it has to end, because a
    # scenario that generates for ever is still running when the next one is
    # being presented.
    settle_minutes: int = Field(default=3, gt=0)

    # How long a leaking shop has been leaking by the time it is seeded, which
    # is the same trick `onset_backdate_minutes` plays and needs a bigger number
    # for a different reason. A flag fault steps: one minute of it is already a
    # departure. A leak ramps, so the departure has to be *accumulated* - and
    # what locates its onset is the contrast between a quiet opening and a climb,
    # which means both have to be in the window. Thirty minutes puts the heap at
    # about two thirds of its limit when anybody first looks: memory well clear
    # of its baseline, latency just beginning to follow, and the error rate not
    # moved at all.
    leak_backdate_minutes: int = Field(default=30, gt=0)


def the_deployed_cache_endpoint(values_file: Path = VALUES_FILE) -> CacheEndpoint:
    """Where the deployment says the cache is.

    Read from the values file on every call rather than cached, because the
    whole point of configuration is that it can change under a running service
    - a platform that syncs a new ConfigMap expects the next request to use it,
    and a value read once at import would make this deployment's most
    changeable setting its least.

    Raises rather than falling back to a default. A shop that quietly invented
    an address when its configuration was missing would be a shop whose
    configuration does not decide anything, and the one incident this file
    exists to make possible is the one where the address is wrong.
    """
    values: dict[str, Any] = yaml.safe_load(values_file.read_text(encoding="utf-8"))
    cache = values[_CACHE]

    return CacheEndpoint(host=cache[_HOST], port=int(cache[_PORT]))


@lru_cache
def get_unleash_settings() -> UnleashSettings:
    return UnleashSettings()


@lru_cache
def get_scenario_settings() -> ScenarioSettings:
    return ScenarioSettings()
