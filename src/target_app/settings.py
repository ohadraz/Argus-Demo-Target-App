from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    admin_token: str = Field(default="*:*.argus-demo-admin-token")
    frontend_token: str = Field(default="default:production.argus-demo-frontend-token")


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


@lru_cache
def get_unleash_settings() -> UnleashSettings:
    return UnleashSettings()


@lru_cache
def get_scenario_settings() -> ScenarioSettings:
    return ScenarioSettings()
