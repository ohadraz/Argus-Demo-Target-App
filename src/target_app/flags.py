from __future__ import annotations

import time

import httpx

from target_app.settings import UnleashSettings, get_unleash_settings

# What a flag needs so that enabling its environment actually makes it evaluate
# true. Unleash treats "the environment is on" and "some strategy matches" as
# two separate conditions, and a flag with no strategy fails the second one
# silently - the evaluation response simply omits it, which reads exactly like
# a flag that is off. Everything downstream would then be waiting on a toggle
# that can never take effect.
_FULL_ROLLOUT_STRATEGY = "flexibleRollout"
_FULL_ROLLOUT_PARAMETERS = {"rollout": "100", "stickiness": "default"}

# How long a change made through the admin API may take to show up in the
# evaluation endpoint. Measured at 0.2-1.0s against the pinned provider; the
# allowance is wide enough that a loaded machine does not turn the lag into a
# failure, and short enough that a provider which has genuinely stopped
# applying changes is reported rather than waited on.
_EVALUATION_LAG_ALLOWANCE_SECONDS = 10.0
_EVALUATION_POLL_SECONDS = 0.1


class FlagProviderUnavailable(Exception):
    """The flag provider could not be reached or refused the request.

    Deliberately not "the flag is off". An unreachable provider and a disabled
    flag are opposite facts, and the service that reports the first as the
    second manufactures a healthy-looking incident-free reading out of an
    outage - which is the same mistake, one layer down, that Argus's own change
    channel refuses to make.
    """


class FlagClient:
    """Reads and changes one flag's state in Unleash.

    Scoped to a single configured flag rather than being a general Unleash
    client, because a single flag is all this service has: the flag is the
    seeded condition a scenario stages, and everything here exists to put it
    into a known state or find out what state it is in.

    Evaluation goes through the Frontend API and state changes through the
    admin API - two different endpoints holding two different powers, which is
    why they take two different tokens.

    The `client` is injected so callers can supply their own transport; it
    defaults to a plain `httpx.Client` with a short timeout, because every call
    here sits in the path of a request someone is waiting on.
    """

    def __init__(
        self,
        settings: UnleashSettings | None = None,
        client: httpx.Client | None = None,
        evaluation_lag_allowance_seconds: float = _EVALUATION_LAG_ALLOWANCE_SECONDS,
    ) -> None:
        self._settings = settings or get_unleash_settings()
        self._client = client or httpx.Client(timeout=5.0)
        self._evaluation_lag_allowance_seconds = evaluation_lag_allowance_seconds

    @property
    def name(self) -> str:
        """Which flag this client speaks for.

        Public because a caller holding two clients has to be able to say which
        one a change was about - "a flag moved" is not a fact anybody watching
        an incident can use when two of them are suspects.
        """
        return self._settings.flag

    def is_enabled(self) -> bool:
        """Whether the flag currently evaluates true, asked at the moment of
        the call.

        Read fresh every time rather than from a cached copy: the whole point
        of the flag is that anyone - a human in Unleash's console, an agent
        through its API - can change it, and this service is supposed to notice.
        Measured propagation from an admin toggle to this answer is under a
        second, so there is nothing here worth caching around.

        A flag that evaluates false is *absent* from the response rather than
        present with `enabled: false`, which is why this asks whether the name
        appears at all.
        """
        response = self._get(
            "/api/frontend", headers={"Authorization": self._settings.frontend_token}
        )
        toggles = response.json().get("toggles", [])
        return any(toggle.get("name") == self._settings.flag for toggle in toggles)

    def enable(self) -> None:
        """Turns the flag on in its environment, returning once it evaluates
        true."""
        self._post(f"{self._environment_path()}/on")
        self._wait_until_evaluates(True)

    def disable(self) -> None:
        """Turns the flag off in its environment, returning once it evaluates
        false."""
        self._post(f"{self._environment_path()}/off")
        self._wait_until_evaluates(False)

    def _wait_until_evaluates(self, expected: bool) -> None:
        """Blocks until evaluation agrees with the change just made.

        An admin toggle does not reach the evaluation endpoint instantly -
        measured at 0.2 to 1.0 seconds. Without this, a caller that turns the
        flag on and immediately asks whether it is on gets `False`, which is
        both wrong and intermittently wrong.

        Waiting belongs here rather than in each caller. There is exactly one
        correct thing for every caller to do about this lag, none of them can
        do anything useful during it, and a caller that forgets produces a
        flake rather than an error.
        """
        deadline = time.monotonic() + self._evaluation_lag_allowance_seconds

        while self.is_enabled() != expected:
            if time.monotonic() >= deadline:
                raise FlagProviderUnavailable(
                    f"flag {self._settings.flag!r} was set to enabled={expected}, but "
                    f"evaluation still disagrees after "
                    f"{self._evaluation_lag_allowance_seconds}s"
                )
            time.sleep(_EVALUATION_POLL_SECONDS)

    def ensure_only_one_environment(self) -> None:
        """Leaves the project carrying the configured environment and no other.

        Unleash ships with `development` and `production`, and a flag created in
        a project inherits every environment that project has. Two environments
        means the flag's page shows two toggles, only one of which does
        anything - which is a question to answer in the middle of a demo, and a
        way to revert the wrong one when it matters.

        The configured environment is added before the others are removed, and
        never removed itself. Removing a project's last environment leaves the
        flag with no environment at all, which is unrecoverable without
        recreating it - there would be nothing left to toggle.
        """
        # Already present is 409, which is this method's goal rather than its
        # failure; every other error still raises.
        self._post_allowing(
            f"/api/admin/projects/{self._settings.project}/environments",
            allowed_statuses=(httpx.codes.CONFLICT,),
            json={"environment": self._settings.environment},
        )

        for environment in self._all_environment_names():
            if environment == self._settings.environment:
                continue
            self._delete(
                f"/api/admin/projects/{self._settings.project}"
                f"/environments/{environment}"
            )

    def ensure_flag_exists(self, description: str) -> bool:
        """Creates the flag and its rollout strategy if they are not there
        already, leaving an existing flag alone. Answers whether it created it.

        Both halves are conditional, and for different reasons. Creating a flag
        that already exists is refused by the provider. Adding a strategy that
        already exists is *not* refused - it appends a second identical one, so
        an unconditional call would stack another copy on every restart.

        Idempotent by construction rather than by exception handling: a service
        that starts, crashes and starts again should reach the same provider
        state as one that started once.

        The answer matters because some flags do not rest where a new flag
        starts. A flag this service just brought into being can be moved to
        where it belongs; one that was already there is somebody else's
        business, and may be sitting mid-incident.

        The description is the caller's because this shop has two flags with
        two different stories, and a client scoped to one flag has no way to
        tell which. Both are read by a human in the provider's own console
        during a demo, so the wrong one is a wrong answer on screen.
        """
        existing = self._find_flag()
        created = existing is None

        if existing is None:
            # An archived flag still owns its name: the provider answers 404 to
            # a lookup and 409 to the creation that follows, which is how a
            # service that only knew how to create one ends up unable to start
            # over a flag that is right there. Archiving is what tidying up
            # looks like here - a test suite clearing the provider between
            # cases does exactly this - so it is an ordinary state to meet, and
            # reviving is the honest answer: the shop's own flags are not
            # optional, and a revived flag is the same flag.
            response = self._post_allowing(
                f"/api/admin/projects/{self._settings.project}/features",
                allowed_statuses=(httpx.codes.CONFLICT,),
                json={
                    "name": self._settings.flag,
                    "type": "release",
                    "description": description,
                },
            )

            if response.status_code == httpx.codes.CONFLICT:
                self._post(f"/api/admin/archive/revive/{self._settings.flag}")

            existing = self._find_flag()

        if not self._has_rollout_strategy(existing):
            self._post(
                f"{self._environment_path()}/strategies",
                json={
                    "name": _FULL_ROLLOUT_STRATEGY,
                    "parameters": {
                        **_FULL_ROLLOUT_PARAMETERS,
                        "groupId": self._settings.flag,
                    },
                },
            )

        return created

    def wait_until_reachable(self, timeout_seconds: float = 60.0) -> None:
        """Blocks until the provider answers, or gives up and raises.

        Startup ordering in the compose stack already waits for the provider's
        healthcheck, so this is the belt to that braces: a service started by
        hand, or restarted while the provider is still running migrations,
        should wait rather than crash-loop.
        """
        deadline = time.monotonic() + timeout_seconds

        while True:
            try:
                self._get("/health")
                return
            except FlagProviderUnavailable:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1.0)

    def _environment_path(self) -> str:
        return (
            f"/api/admin/projects/{self._settings.project}"
            f"/features/{self._settings.flag}"
            f"/environments/{self._settings.environment}"
        )

    def _all_environment_names(self) -> list[str]:
        response = self._get(
            "/api/admin/environments",
            headers={"Authorization": self._settings.admin_token},
        )
        environments = response.json().get("environments", [])

        return [
            name
            for environment in environments
            if isinstance(name := environment.get("name"), str)
        ]

    def _find_flag(self) -> dict[str, object] | None:
        """The flag's admin representation, or `None` if the provider has no
        such flag. A 404 here is an answer, not a failure."""
        url = (
            f"{self._settings.base_url}/api/admin/projects/"
            f"{self._settings.project}/features/{self._settings.flag}"
        )
        try:
            response = self._client.get(
                url, headers={"Authorization": self._settings.admin_token}
            )
        except httpx.HTTPError as error:
            raise FlagProviderUnavailable(f"could not reach {url}: {error}") from error

        if response.status_code == httpx.codes.NOT_FOUND:
            return None

        self._raise_for_status(response, url)
        return dict(response.json())

    def _has_rollout_strategy(self, flag: dict[str, object] | None) -> bool:
        if flag is None:
            return False

        environments = flag.get("environments")
        if not isinstance(environments, list):
            return False

        return any(
            environment.get("name") == self._settings.environment
            and environment.get("strategies")
            for environment in environments
            if isinstance(environment, dict)
        )

    def _get(self, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
        url = f"{self._settings.base_url}{path}"
        try:
            response = self._client.get(url, headers=headers)
        except httpx.HTTPError as error:
            raise FlagProviderUnavailable(f"could not reach {url}: {error}") from error

        self._raise_for_status(response, url)
        return response

    def _post(self, path: str, json: dict[str, object] | None = None) -> httpx.Response:
        return self._post_allowing(path, allowed_statuses=(), json=json)

    def _post_allowing(
        self,
        path: str,
        allowed_statuses: tuple[int, ...],
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        """POSTs, treating the named error statuses as success.

        For the requests whose failure mode *is* the desired state - adding
        something already added. Narrower than catching every error, so a 403
        or a 500 on the same call still surfaces.
        """
        url = f"{self._settings.base_url}{path}"
        try:
            response = self._client.post(
                url, headers={"Authorization": self._settings.admin_token}, json=json
            )
        except httpx.HTTPError as error:
            raise FlagProviderUnavailable(f"could not reach {url}: {error}") from error

        if response.status_code in allowed_statuses:
            return response

        self._raise_for_status(response, url)
        return response

    def _delete(self, path: str) -> httpx.Response:
        url = f"{self._settings.base_url}{path}"
        try:
            response = self._client.delete(
                url, headers={"Authorization": self._settings.admin_token}
            )
        except httpx.HTTPError as error:
            raise FlagProviderUnavailable(f"could not reach {url}: {error}") from error

        self._raise_for_status(response, url)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response, url: str) -> None:
        if response.is_error:
            raise FlagProviderUnavailable(
                f"{url} answered {response.status_code}: {response.text[:200]}"
            )
