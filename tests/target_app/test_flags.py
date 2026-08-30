from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest
from target_app.flags import FlagClient, FlagProviderUnavailable
from target_app.settings import UnleashSettings

"""What the flag client promises about a provider it does not control.

Every test here drives a doubled transport rather than a live Unleash. The round
trip against the real provider is worth making and was made by hand, but it
cannot express the cases that matter most: a provider that is down, one that
answers 500, or a flag whose evaluation lags the write that caused it. Those are
the readings the service must not misinterpret, and they are unreachable from a
healthy container.
"""

SOME_FLAG = "monthly-spend-feature"
SOME_ENVIRONMENT = "production"
# Shown in the provider's console and nowhere these tests look.
DONT_CARE_DESCRIPTION = "what this flag is for"


def a_settings() -> UnleashSettings:
    return UnleashSettings(
        base_url="http://unleash.test",
        project="default",
        environment=SOME_ENVIRONMENT,
        flag=SOME_FLAG,
        admin_token="dont-care-admin-token",
        frontend_token="dont-care-frontend-token",
    )


def an_evaluation_listing(*enabled_flags: str) -> httpx.Response:
    """The Frontend API's answer, in which a disabled flag simply does not
    appear."""
    return httpx.Response(
        200,
        json={"toggles": [{"name": flag, "enabled": True} for flag in enabled_flags]},
    )


def a_flag_carrying_strategies(count: int) -> httpx.Response:
    """The admin API's view of a flag, whose environment has that many
    strategies."""
    return httpx.Response(
        200,
        json={
            "name": SOME_FLAG,
            "environments": [
                {
                    "name": SOME_ENVIRONMENT,
                    "enabled": False,
                    "strategies": [{"name": "flexibleRollout"}] * count,
                },
                {"name": "development", "enabled": False, "strategies": []},
            ],
        },
    )


def a_transport_answering(get: httpx.Response) -> Mock:
    transport = Mock(spec=httpx.Client)
    transport.get.return_value = get
    transport.post.return_value = httpx.Response(200, json={})
    return transport


def posted_paths(transport: Mock) -> list[str]:
    return [str(call.args[0]) for call in transport.post.call_args_list]


def test_a_flag_absent_from_the_evaluation_reads_as_off() -> None:
    # Unleash omits a disabled flag rather than reporting `enabled: false`, so
    # "off" is an absence. A client looking for a field that is never there
    # would read every flag as off, including the ones that are on.
    some_unrelated_flag = "another-flag"
    transport = a_transport_answering(an_evaluation_listing(some_unrelated_flag))

    assert FlagClient(settings=a_settings(), client=transport).is_enabled() is False


def test_a_flag_present_in_the_evaluation_reads_as_on() -> None:
    transport = a_transport_answering(an_evaluation_listing(SOME_FLAG))

    assert FlagClient(settings=a_settings(), client=transport).is_enabled() is True


def test_an_unreachable_provider_raises_rather_than_reading_as_off() -> None:
    # The failure this exists to prevent: an outage in the flag provider
    # arriving downstream as "the flag is off", which has the same shape as a
    # healthy service and would erase an incident rather than report one.
    transport = Mock(spec=httpx.Client)
    transport.get.side_effect = httpx.ConnectError("connection refused")

    with pytest.raises(FlagProviderUnavailable):
        FlagClient(settings=a_settings(), client=transport).is_enabled()


def test_an_error_response_raises_rather_than_reading_as_off() -> None:
    transport = a_transport_answering(httpx.Response(500, text="upstream boom"))

    with pytest.raises(FlagProviderUnavailable):
        FlagClient(settings=a_settings(), client=transport).is_enabled()


def test_bootstrapping_creates_a_missing_flag_and_gives_it_a_strategy() -> None:
    transport = Mock(spec=httpx.Client)
    transport.get.side_effect = [httpx.Response(404), a_flag_carrying_strategies(0)]
    transport.post.return_value = httpx.Response(200, json={})

    FlagClient(settings=a_settings(), client=transport).ensure_flag_exists(DONT_CARE_DESCRIPTION)

    assert any(path.endswith("/features") for path in posted_paths(transport))
    assert any(path.endswith("/strategies") for path in posted_paths(transport))


def test_bootstrapping_revives_a_flag_that_was_archived() -> None:
    # An archived flag still owns its name: the provider answers 404 to the
    # lookup and 409 to the creation that follows. A service that only knew how
    # to create one is then unable to start over a flag that is right there -
    # and archiving is ordinary here, being what a suite clearing the provider
    # between cases does.
    transport = Mock(spec=httpx.Client)
    transport.get.side_effect = [httpx.Response(404), a_flag_carrying_strategies(1)]
    transport.post.side_effect = [
        httpx.Response(409, json={"name": "NameExistsError"}),
        httpx.Response(200, json={}),
    ]

    FlagClient(settings=a_settings(), client=transport).ensure_flag_exists(
        DONT_CARE_DESCRIPTION
    )

    assert any("/archive/revive/" in path for path in posted_paths(transport))


def test_a_flag_the_provider_refuses_to_create_is_not_swallowed() -> None:
    # Only the name clash is an ordinary state to meet. Every other refusal is
    # a provider saying something is wrong, and a bootstrap that carried on
    # would leave the service serving a flag it never established.
    transport = Mock(spec=httpx.Client)
    transport.get.return_value = httpx.Response(404)
    transport.post.return_value = httpx.Response(500, text="upstream boom")

    with pytest.raises(FlagProviderUnavailable):
        FlagClient(settings=a_settings(), client=transport).ensure_flag_exists(
            DONT_CARE_DESCRIPTION
        )


def test_bootstrapping_leaves_an_established_flag_alone() -> None:
    transport = a_transport_answering(a_flag_carrying_strategies(1))

    FlagClient(settings=a_settings(), client=transport).ensure_flag_exists(DONT_CARE_DESCRIPTION)

    transport.post.assert_not_called()


def test_bootstrapping_twice_does_not_stack_a_second_strategy() -> None:
    # Adding a strategy appends rather than replaces, and the provider accepts
    # a duplicate without complaint. An unconditional add would leave a service
    # that has restarted twice carrying three identical strategies.
    transport = a_transport_answering(a_flag_carrying_strategies(1))

    FlagClient(settings=a_settings(), client=transport).ensure_flag_exists(DONT_CARE_DESCRIPTION)

    assert not any(path.endswith("/strategies") for path in posted_paths(transport))


def test_bootstrapping_repairs_a_flag_that_has_no_strategy() -> None:
    # The trap this exists for: a flag can exist, and its environment can be
    # enabled, and it still evaluates false because no strategy matches. Left
    # unrepaired, mitigation would revert a flag that was never really on.
    transport = a_transport_answering(a_flag_carrying_strategies(0))

    FlagClient(settings=a_settings(), client=transport).ensure_flag_exists(DONT_CARE_DESCRIPTION)

    assert any(path.endswith("/strategies") for path in posted_paths(transport))
    assert not any(path.endswith("/features") for path in posted_paths(transport))


def test_enabling_waits_for_the_evaluation_to_agree() -> None:
    # The provider applies an admin write before its evaluation endpoint
    # reflects it. A client that returned on the write would tell its caller
    # the flag is on while the very next read still says off.
    transport = Mock(spec=httpx.Client)
    transport.post.return_value = httpx.Response(200, json={})
    transport.get.side_effect = [
        an_evaluation_listing(),
        an_evaluation_listing(),
        an_evaluation_listing(SOME_FLAG),
    ]

    FlagClient(settings=a_settings(), client=transport).enable()

    reads_until_the_write_showed_up = 3
    assert transport.get.call_count == reads_until_the_write_showed_up


def test_disabling_waits_for_the_evaluation_to_agree() -> None:
    transport = Mock(spec=httpx.Client)
    transport.post.return_value = httpx.Response(200, json={})
    transport.get.side_effect = [
        an_evaluation_listing(SOME_FLAG),
        an_evaluation_listing(),
    ]

    FlagClient(settings=a_settings(), client=transport).disable()

    reads_until_the_write_showed_up = 2
    assert transport.get.call_count == reads_until_the_write_showed_up


def test_enabling_gives_up_when_the_evaluation_never_agrees() -> None:
    # A provider that accepts writes and never applies them is broken, and
    # saying so beats blocking a request thread forever.
    no_patience = 0.0
    transport = Mock(spec=httpx.Client)
    transport.post.return_value = httpx.Response(200, json={})
    transport.get.return_value = an_evaluation_listing()

    with pytest.raises(FlagProviderUnavailable):
        FlagClient(
            settings=a_settings(),
            client=transport,
            evaluation_lag_allowance_seconds=no_patience,
        ).enable()
