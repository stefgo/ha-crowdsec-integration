"""What a diagnostics download hands out — and what it does not."""

from __future__ import annotations

from custom_components.crowdsec.api import CrowdSecConnectionError
from custom_components.crowdsec.diagnostics import (
    _redact_host,
    async_get_config_entry_diagnostics,
)
from custom_components.crowdsec.metrics import MetricSet, parse_prometheus

from .conftest import METRICS_TEXT, make_alert, make_decision


async def test_the_secrets_are_gone(hass, loaded_entry):
    result = await async_get_config_entry_diagnostics(hass, loaded_entry)

    config = result["config"]
    assert config["machine_password"] == "**REDACTED**"
    assert config["machine_id"] == "**REDACTED**"
    assert config["bouncer_api_key"] == "**REDACTED**"


async def test_the_host_is_gone_but_the_shape_stays(hass, loaded_entry):
    """Diagnostics get pasted into public issues; internal names must not."""
    result = await async_get_config_entry_diagnostics(hass, loaded_entry)

    assert result["config"]["lapi_url"] == "http://**REDACTED**:8080"
    assert result["config"]["metrics_url"] == "http://**REDACTED**:6060/metrics"


async def test_the_banned_addresses_are_gone(hass, loaded_entry, fake_client):
    fake_client.decisions = [make_decision(1, ip="192.0.2.77")]
    fake_client.alerts = [make_alert(1, ip="192.0.2.77")]
    await loaded_entry.runtime_data.async_refresh()

    result = await async_get_config_entry_diagnostics(hass, loaded_entry)

    assert result["data"]["top_attacker"] == "**REDACTED**"
    assert all(row["value"] == "**REDACTED**" for row in result["data"]["decisions"])
    # The shape of a row is what a support question is about — it stays.
    assert "status" in result["data"]["decisions"][0]
    assert "192.0.2.77" not in str(result)


async def test_the_addresses_hidden_in_the_row_keys_are_gone_too(
    hass, loaded_entry, fake_client
):
    """``value`` is not the only field an address reaches the outside in.

    Two of the three key shapes are assembled from the address itself, so a
    row whose ``value`` is redacted can still carry it in its ``key``.
    """
    # No ID: the key becomes "val:<origin>:<ip>".
    fake_client.decisions = [
        {
            "origin": "cscli",
            "type": "ban",
            "scope": "Ip",
            "value": "192.0.2.88",
            "duration": "3h",
        }
    ]
    # Long enough ago that its 4h ban has run out — that is what puts a row
    # into the history, where the key becomes "hist:<ip>:<scenario>:<time>".
    fake_client.alerts = [make_alert(2, ip="198.51.100.7", minutes_ago=1000)]
    await loaded_entry.runtime_data.async_refresh()

    rows = (await async_get_config_entry_diagnostics(hass, loaded_entry))["data"][
        "decisions"
    ]
    keys = {row["key"] for row in rows}
    assert keys == {"val:**REDACTED**", "hist:**REDACTED**"}

    result = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert "192.0.2.88" not in str(result)
    assert "198.51.100.7" not in str(result)


async def test_a_decision_id_stays_readable(hass, loaded_entry, fake_client):
    """The ID is the one key that is not built from the address."""
    fake_client.decisions = [make_decision(42, ip="192.0.2.99")]
    await loaded_entry.runtime_data.async_refresh()

    result = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert result["data"]["decisions"][0]["key"] == "id:42"
    assert "192.0.2.99" not in str(result)


async def test_the_raw_metrics_are_included(hass, loaded_entry):
    """Without them there is no telling what CrowdSec actually returned."""
    result = await async_get_config_entry_diagnostics(hass, loaded_entry)

    assert "cs_active_decisions" in result["metrics"]


async def test_the_machine_name_is_gone_from_the_metrics(
    hass, loaded_entry, fake_client
):
    """CrowdSec labels the per-machine counter with the redacted machine ID."""
    text = (
        METRICS_TEXT
        + 'cs_lapi_machine_requests_total{machine="hass",route="/v1/alerts",'
        'method="GET"} 12\n'
    )
    fake_client.metrics_fn = lambda: MetricSet(parse_prometheus(text))
    await loaded_entry.runtime_data.async_refresh()

    result = await async_get_config_entry_diagnostics(hass, loaded_entry)

    (sample,) = result["metrics"]["cs_lapi_machine_requests_total"]
    assert sample["labels"] == {
        "machine": "**REDACTED**",
        "route": "/v1/alerts",
        "method": "GET",
    }
    assert sample["value"] == 12


async def test_the_host_is_gone_from_the_error_texts(hass, loaded_entry, fake_client):
    """An error quotes the URL it failed on — the host the config redacts."""
    fake_client.alerts_error = CrowdSecConnectionError(
        "LAPI /v1/alerts failed: Cannot connect to host LOCALHOST:8080 ssl:default"
    )
    await loaded_entry.runtime_data.async_refresh()

    result = await async_get_config_entry_diagnostics(hass, loaded_entry)
    data = result["data"]

    assert data["errors"] == [
        "LAPI /v1/alerts failed: Cannot connect to host **REDACTED**:8080 ssl:default"
    ]
    assert data["problem_reasons"][0] == data["errors"][0]
    assert data["problems"][0]["text"] == data["errors"][0]
    assert "localhost" not in str(result).lower()


def test_redacting_a_host_keeps_scheme_port_and_path():
    assert _redact_host("https://crowdsec.home.example:8443/x") == (
        "https://**REDACTED**:8443/x"
    )
    assert _redact_host("http://10.0.0.5:6060/metrics") == (
        "http://**REDACTED**:6060/metrics"
    )
    # No port, no path.
    assert _redact_host("http://crowdsec") == "http://**REDACTED**"
    # Nothing usable to take apart — then nothing gets through either.
    assert _redact_host("not a url") == "**REDACTED**"
    assert _redact_host("") == ""
    assert _redact_host(None) is None
