"""Tests for the decision routes of the client.

Reading goes through the bouncer key only — CrowdSec serves ``GET
/v1/decisions`` to bouncers alone — so those tests run against a fake session
and check what actually goes over the wire. Deleting uses the machine token;
there the HTTP layer stays out of it.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiohttp")

from crowdsec_component.api import (
    ENDPOINT_BOUNCER,
    CrowdSecAuthError,
    CrowdSecClient,
    CrowdSecConnectionError,
    _deleted_count,
)


class FakeResponse:
    def __init__(self, status: int, body) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def json(self, content_type=None):
        return self._body


class FakeSession:
    """Answers every GET with one canned response and records the requests."""

    def __init__(self, status: int = 200, body=None) -> None:
        self.status = status
        self.body = [] if body is None else body
        self.requests: list[dict] = []

    def get(self, url, params=None, headers=None, **kwargs):
        self.requests.append({"url": url, "params": params, "headers": headers})
        return FakeResponse(self.status, self.body)


def build_client(bouncer_api_key=None, session=None) -> CrowdSecClient:
    """A client whose machine-token path must not be taken for reading."""
    return CrowdSecClient(
        session=session,
        metrics_url="http://localhost:6060/metrics",
        lapi_url="http://localhost:8080",
        machine_id="hass",
        machine_password="secret",
        bouncer_api_key=bouncer_api_key,
    )


def run(coroutine_factory):
    """Run a coroutine built inside the loop (the client needs one)."""
    return asyncio.run(coroutine_factory())


DECISION = {"id": 1, "value": "192.0.2.1", "duration": "4h", "type": "ban"}


# -- Reading the list -------------------------------------------------------


def forbid_machine_token(client: CrowdSecClient) -> None:
    async def fake_request(*args, **kwargs):
        raise AssertionError("the decision list must never use the machine token")

    client._async_lapi_request = fake_request  # type: ignore[assignment]


def test_the_list_is_read_with_the_bouncer_key_only():
    """The machine token would only earn a 403 and a re-login (issue #33)."""
    session = FakeSession(body=[DECISION])

    async def scenario():
        client = build_client(bouncer_api_key="key", session=session)
        forbid_machine_token(client)
        result = await client.async_get_decisions()
        return client, result

    client, result = run(scenario)
    assert result == [DECISION]
    assert client.decisions_need_bouncer_key is False
    (request,) = session.requests
    assert request["url"] == "http://localhost:8080/v1/decisions"
    assert request["headers"]["X-Api-Key"] == "key"
    assert "Authorization" not in request["headers"]
    assert (client.decision_requests, client.bouncer_decision_requests) == (1, 1)


@pytest.mark.parametrize("query", ["list", "lookup"])
def test_without_a_key_nothing_is_sent(query):
    """No request that is bound to fail — the repair asks for a key instead."""
    session = FakeSession()

    async def scenario():
        client = build_client(session=session)
        forbid_machine_token(client)
        if query == "list":
            result = await client.async_get_decisions()
        else:
            result = await client.async_lookup_ip("192.0.2.1")
        return client, result

    client, result = run(scenario)
    # None means "the metric has to take over" — not an empty ban list.
    assert result is None
    assert client.decisions_need_bouncer_key is True
    assert session.requests == []
    assert client.decision_requests == 0


def test_a_404_means_no_decisions():
    """Not every CrowdSec version answers an empty list with an empty array.

    It is an answer, not a failure: the table has to empty, not keep showing
    the rows of the last cycle.
    """

    async def scenario():
        client = build_client(bouncer_api_key="key", session=FakeSession(404))
        return await client.async_get_decisions()

    assert run(scenario) == []


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_bouncer_key_still_surfaces(status):
    """A wrong key is a configuration error and has to be reported."""

    async def scenario():
        client = build_client(bouncer_api_key="wrong", session=FakeSession(status))
        forbid_machine_token(client)
        return await client.async_get_decisions()

    with pytest.raises(CrowdSecAuthError) as err:
        run(scenario)
    assert err.value.endpoint == ENDPOINT_BOUNCER


def test_non_objects_are_dropped_and_junk_is_rejected():
    async def scenario(answer):
        client = build_client(bouncer_api_key="key", session=FakeSession(body=answer))
        return await client.async_get_decisions()

    assert asyncio.run(scenario([DECISION, "nonsense", None])) == [DECISION]
    assert asyncio.run(scenario(None)) == []
    with pytest.raises(CrowdSecConnectionError):
        asyncio.run(scenario({"decisions": []}))


# -- Deleting ---------------------------------------------------------------


def test_delete_targets_a_single_decision():
    calls: list[tuple] = []

    async def scenario():
        client = build_client()

        async def fake_request(method, path, *args, **kwargs):
            calls.append((method, path))
            return {"nbDeleted": "1"}

        client._async_lapi_request = fake_request  # type: ignore[assignment]
        return await client.async_delete_decision(42)

    assert run(scenario) == 1
    assert calls == [("DELETE", "/v1/decisions/42")]


def test_delete_by_address_filters_by_value_only():
    """The delete route knows ``scopes``, not ``scope``, and refuses the latter.

    ``value`` alone matches the decisions naming exactly this address, stored
    as ``Ip`` or ``Range`` alike — and nothing that merely covers it.
    """
    calls: list[tuple] = []

    async def scenario():
        client = build_client()

        async def fake_request(method, path, params=None, *args, **kwargs):
            calls.append((method, path, params))
            return {"nbDeleted": "2"}

        client._async_lapi_request = fake_request  # type: ignore[assignment]
        return await client.async_unban_ip("192.0.2.1")

    assert run(scenario) == 2
    assert calls == [("DELETE", "/v1/decisions", {"value": "192.0.2.1"})]


def ban_payload(target: str) -> dict:
    calls: list[tuple] = []

    async def scenario():
        client = build_client()

        async def fake_request(method, path, params=None, payload=None, **kwargs):
            calls.append((method, path, payload))
            return None

        client._async_lapi_request = fake_request  # type: ignore[assignment]
        await client.async_ban_ip(target, "4h", "test")

    run(scenario)
    ((method, path, payload),) = calls
    assert (method, path) == ("POST", "/v1/alerts")
    (alert,) = payload
    return alert


def test_an_address_is_banned_with_the_ip_scope():
    alert = ban_payload("192.0.2.1")
    assert alert["source"]["scope"] == "Ip"
    assert alert["decisions"][0]["scope"] == "Ip"


def test_a_range_is_banned_with_the_range_scope():
    """The scope ``cscli decisions add --range`` gives it, not ``Ip``."""
    alert = ban_payload("10.0.0.0/24")
    assert alert["source"]["scope"] == "Range"
    assert alert["decisions"][0] == {
        **alert["decisions"][0],
        "scope": "Range",
        "value": "10.0.0.0/24",
        "duration": "4h",
    }


def test_deleted_count_reads_both_shapes():
    # CrowdSec sends the count as a string in some versions, as a number in
    # others — both mean the same thing.
    assert _deleted_count({"nbDeleted": "3"}) == 3
    assert _deleted_count({"nbDeleted": 3}) == 3
    assert _deleted_count({"nbDeleted": None}) == 0
    assert _deleted_count({}) == 0
    assert _deleted_count(None) == 0


# -- Checking the key -------------------------------------------------------


def test_the_key_is_checked_without_fetching_the_list():
    """With a blocklist the list is huge; one address answers the question."""
    session = FakeSession(body=None)

    async def scenario():
        client = build_client(bouncer_api_key="key", session=session)
        forbid_machine_token(client)
        await client.async_check_bouncer_key()
        return client

    client = run(scenario)
    (request,) = session.requests
    assert request["params"] == {"ip": "192.0.2.1"}
    assert request["headers"]["X-Api-Key"] == "key"
    # A candidate key says nothing about the running entry's key.
    assert client.decisions_need_bouncer_key is False


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_fails_the_check(status):
    async def scenario():
        client = build_client(bouncer_api_key="wrong", session=FakeSession(status))
        await client.async_check_bouncer_key()

    with pytest.raises(CrowdSecAuthError) as err:
        run(scenario)
    assert err.value.endpoint == ENDPOINT_BOUNCER


# -- Restricting to origins -------------------------------------------------


def test_origins_are_passed_to_the_lapi():
    """The filter is what keeps a subscribed blocklist out of every cycle."""
    session = FakeSession(body=[DECISION])

    async def scenario():
        client = build_client(bouncer_api_key="key", session=session)
        return await client.async_get_decisions(("cscli", "crowdsec"))

    assert run(scenario) == [DECISION]
    assert session.requests[0]["params"] == {"origins": "cscli,crowdsec"}


# -- Looking one address up -------------------------------------------------


def lookup_params(target: str) -> dict:
    session = FakeSession(body=[DECISION])

    async def scenario():
        client = build_client(bouncer_api_key="key", session=session)
        forbid_machine_token(client)
        return await client.async_lookup_ip(target)

    assert run(scenario) == [DECISION]
    (request,) = session.requests
    assert request["headers"]["X-Api-Key"] == "key"
    return request["params"]


def test_a_lookup_asks_for_everything_covering_the_address():
    """contains=true is what finds the /24 an address sits in."""
    assert lookup_params("192.0.2.1") == {"ip": "192.0.2.1", "contains": "true"}


def test_a_range_is_looked_up_as_a_range():
    """The LAPI has its own parameter for it; ip= would not match."""
    assert lookup_params("10.0.0.0/24") == {"range": "10.0.0.0/24", "contains": "true"}


def test_a_lookup_ignores_the_configured_scope():
    """The question is whether the address is blocked at all, by anyone."""
    assert "origins" not in lookup_params("192.0.2.1")


def test_the_alert_lookup_carries_the_window_and_a_small_limit():
    calls: list[tuple] = []

    async def scenario():
        client = build_client()

        async def fake_request(method, path, params=None, *args, **kwargs):
            calls.append((method, path, params))
            return [{"id": 1}]

        client._async_lapi_request = fake_request  # type: ignore[assignment]
        return await client.async_lookup_alerts("192.0.2.1")

    assert run(scenario) == [{"id": 1}]
    method, path, params = calls[0]
    assert (method, path) == ("GET", "/v1/alerts")
    assert params["ip"] == "192.0.2.1"
    assert params["limit"] == "50"
