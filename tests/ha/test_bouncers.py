"""Bouncers in the update cycle and as entities, and the structured problems."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er

from custom_components.crowdsec.api import CrowdSecConnectionError
from custom_components.crowdsec.const import (
    CONF_BOUNCER_IDLE_INTERVALS,
    CONF_PARSE_ERROR_THRESHOLD,
    DOMAIN,
)
from custom_components.crowdsec.metrics import MetricSet, parse_prometheus

from .conftest import METRICS_TEXT

TRAEFIK = "binary_sensor.crowdsec_bouncer_traefik"
FIREWALL = "binary_sensor.crowdsec_bouncer_fw"
ACTIVE_BOUNCERS = "sensor.crowdsec_active_bouncers"
STATUS = "binary_sensor.crowdsec_status"
QUERIES = "sensor.crowdsec_bouncer_queries_per_minute"


def per_bouncer_metrics(counts: dict[str, float]) -> MetricSet:
    """The fixture's metrics plus the counters per client.

    The machine counter is always there: the integration logs in as a machine
    every cycle, so a CrowdSec that counts per client has it.
    """
    lines = [
        METRICS_TEXT,
        'cs_lapi_machine_requests_total{machine="hass",route="/v1/alerts",method="GET"} 9',
    ]
    lines += [
        f'cs_lapi_bouncer_requests_total{{bouncer="{name}",'
        f'route="/v1/decisions/stream",method="GET"}} {value}'
        for name, value in counts.items()
    ]
    return MetricSet(parse_prometheus("\n".join(lines)))


def summed_metrics(route_total: float) -> MetricSet:
    """Only the sum over all clients, as at ``prometheus.level: aggregated``."""
    text = METRICS_TEXT.replace(
        'endpoint="/v1/decisions/stream",method="GET"} 50',
        f'endpoint="/v1/decisions/stream",method="GET"}} {route_total}',
    )
    return MetricSet(parse_prometheus(text))


async def setup(hass, config_entry, fake_client, **options):
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options=options)
    with (
        patch("custom_components.crowdsec.build_client", return_value=fake_client),
        patch(
            "custom_components.crowdsec._async_register_card",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry.runtime_data


async def refresh(hass, coordinator, times: int = 1):
    for _ in range(times):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    return coordinator.data


def problem_types(data) -> list[str]:
    return [problem["type"] for problem in data.problems]


# -- The integration's own queries ------------------------------------------


async def test_own_decision_queries_are_not_bouncer_queries(
    hass, config_entry, fake_client
):
    """The bug this is about: the sum never dropped to 0 while HA was running.

    The route metric counts the integration's own decision query like any
    bouncer's. With no bouncer left, only those remain — and that has to read
    as "no bouncer queries".
    """
    fake_client.metrics_fn = lambda: summed_metrics(50 + fake_client.decision_requests)
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_BOUNCER_IDLE_INTERVALS: 2}
    )

    data = await refresh(hass, coordinator, times=3)

    assert data.per_bouncer is False
    assert data.bouncers == []
    assert data.bouncer_queries_per_minute == 0
    assert "no-bouncer-queries" in problem_types(data)
    assert hass.states.get(ACTIVE_BOUNCERS).state == "unknown"


async def test_a_bouncer_in_the_sum_still_counts(hass, config_entry, fake_client):
    cycles = {"n": 0}

    def metrics():
        cycles["n"] += 1
        return summed_metrics(50 + fake_client.decision_requests + 6 * cycles["n"])

    fake_client.metrics_fn = metrics
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_BOUNCER_IDLE_INTERVALS: 2}
    )

    data = await refresh(hass, coordinator, times=3)

    assert data.bouncer_queries_per_minute > 0
    assert data.problem is False


async def test_the_decision_query_follows_the_scrape_without_counters_per_bouncer(
    hass, config_entry, fake_client
):
    """Only then is it clear which side of the scrape the own query landed on."""
    order: list[str] = []
    get_metrics = fake_client.async_get_metrics
    get_decisions = fake_client.async_get_decisions

    async def metrics():
        order.append("metrics")
        return await get_metrics()

    async def decisions(origins=None):
        order.append("decisions")
        return await get_decisions(origins)

    fake_client.async_get_metrics = metrics
    fake_client.async_get_decisions = decisions
    await setup(hass, config_entry, fake_client)

    assert order[:2] == ["metrics", "decisions"]


# -- Per bouncer ------------------------------------------------------------


async def test_one_stopped_bouncer_is_reported_by_name(hass, config_entry, fake_client):
    counts = {"traefik": 10.0, "fw": 100.0}

    def metrics():
        counts["traefik"] += 1
        return per_bouncer_metrics(counts)

    fake_client.metrics_fn = metrics
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_BOUNCER_IDLE_INTERVALS: 2}
    )
    # fw queries once and then never again.
    counts["fw"] += 1
    await refresh(hass, coordinator)

    data = await refresh(hass, coordinator, times=2)

    assert data.per_bouncer is True
    assert hass.states.get(TRAEFIK).state == "on"
    assert hass.states.get(FIREWALL).state == "off"
    assert hass.states.get(FIREWALL).attributes["idle_intervals"] == 2

    active = hass.states.get(ACTIVE_BOUNCERS)
    assert active.state == "1"
    assert active.attributes["total"] == 2
    assert [b["name"] for b in active.attributes["bouncers"]] == ["fw", "traefik"]

    status = hass.states.get(STATUS)
    assert status.state == "on"
    (problem,) = status.attributes["problems"]
    assert problem["type"] == "bouncer-idle"
    assert problem["severity"] == "error"
    assert problem["bouncer"] == "fw"
    assert status.attributes["reasons"] == [problem["text"]]
    # The sum covers the bouncers only: one query per cycle from traefik.
    assert float(hass.states.get(QUERIES).state) > 0


async def test_a_disabled_bouncer_is_ignored(hass, config_entry, fake_client):
    counts = {"traefik": 10.0, "homeassistant": 1.0}

    def metrics():
        counts["traefik"] += 1
        return per_bouncer_metrics(counts)

    fake_client.metrics_fn = metrics
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_BOUNCER_IDLE_INTERVALS: 1}
    )
    counts["homeassistant"] += 1
    await refresh(hass, coordinator)
    await refresh(hass, coordinator)
    assert (
        hass.states.get("binary_sensor.crowdsec_bouncer_homeassistant").state == "off"
    )

    registry = er.async_get(hass)
    registry.async_update_entity(
        "binary_sensor.crowdsec_bouncer_homeassistant",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    await hass.async_block_till_done()
    data = await refresh(hass, coordinator)

    assert [status.name for status in data.bouncers] == ["traefik"]
    assert data.problem is False
    assert hass.states.get(ACTIVE_BOUNCERS).attributes["total"] == 1
    # Not brought back as an entity either.
    assert hass.states.get("binary_sensor.crowdsec_bouncer_homeassistant") is None


async def test_a_known_bouncer_survives_a_restart(hass, config_entry, fake_client):
    """After a restart of CrowdSec its counter is missing — it must still go off."""
    config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{config_entry.entry_id}_bouncer_traefik",
        config_entry=config_entry,
        suggested_object_id="crowdsec_bouncer_traefik",
    )
    fake_client.metrics_fn = lambda: per_bouncer_metrics({})
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_BOUNCER_IDLE_INTERVALS: 2}
    )

    # Known, but nothing seen yet.
    assert hass.states.get(TRAEFIK).state == "unknown"

    await refresh(hass, coordinator, times=2)

    assert hass.states.get(TRAEFIK).state == "off"
    assert "bouncer-idle" in problem_types(coordinator.data)


async def test_without_any_bouncer_nothing_is_enforced(hass, config_entry, fake_client):
    fake_client.metrics_fn = lambda: per_bouncer_metrics({})
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_BOUNCER_IDLE_INTERVALS: 2}
    )

    data = await refresh(hass, coordinator, times=2)

    assert data.per_bouncer is True
    assert hass.states.get(ACTIVE_BOUNCERS).state == "0"
    assert problem_types(data) == ["no-bouncer-queries"]


async def test_a_deleted_bouncer_stays_ignored(hass, config_entry, fake_client):
    """Deleted is forgotten; if it queries again, Home Assistant restores it disabled.

    The entity registry remembers deleted entities and brings their settings
    back with them — ``disabled_by`` included. An entity can only be deleted
    once it is disabled, so a bouncer that returns stays ignored.
    """
    counts = {"old": 1.0}
    fake_client.metrics_fn = lambda: per_bouncer_metrics(counts)
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_BOUNCER_IDLE_INTERVALS: 1}
    )
    counts["old"] += 1
    await refresh(hass, coordinator)
    entity_id = "binary_sensor.crowdsec_bouncer_old"
    assert hass.states.get(entity_id) is not None

    # Disabling takes it out of Home Assistant; then it can be deleted.
    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    registry.async_remove(entity_id)
    await hass.async_block_till_done()

    data = await refresh(hass, coordinator, times=2)
    assert data.bouncers == []
    assert registry.async_get(entity_id) is None

    counts["old"] += 1
    await refresh(hass, coordinator)
    data = await refresh(hass, coordinator)

    entry = registry.async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.USER
    assert data.bouncers == []
    assert data.problem is False


async def test_bouncers_follow_the_metrics_scrape(hass, config_entry, fake_client):
    counts = {"traefik": 1.0}

    def metrics():
        counts["traefik"] += 1
        return per_bouncer_metrics(counts)

    fake_client.metrics_fn = metrics
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_BOUNCER_IDLE_INTERVALS: 1}
    )
    await refresh(hass, coordinator)
    assert hass.states.get(TRAEFIK).state == "on"

    fake_client.metrics_error = CrowdSecConnectionError("metrics down")
    data = await refresh(hass, coordinator)

    assert hass.states.get(TRAEFIK).state == "unavailable"
    # Still known, so a stopped bouncer would stay in the problems.
    assert [status.name for status in data.bouncers] == ["traefik"]


# -- Structured problems ----------------------------------------------------


async def test_a_failed_query_is_a_warning(hass, loaded_entry, fake_client):
    coordinator = loaded_entry.runtime_data
    fake_client.alerts_error = CrowdSecConnectionError("alerts down")

    data = await refresh(hass, coordinator)

    assert data.problems == [
        {
            "severity": "warning",
            "type": "query-failed",
            "text": "alerts down",
            "since": data.problems[0]["since"],
        }
    ]
    assert data.problem_reasons == ["alerts down"]


async def test_an_unreachable_instance_is_one_error(hass, loaded_entry, fake_client):
    coordinator = loaded_entry.runtime_data
    fake_client.metrics_error = CrowdSecConnectionError("down")
    fake_client.alerts_error = CrowdSecConnectionError("down")
    fake_client.decisions_error = CrowdSecConnectionError("down")

    data = await refresh(hass, coordinator)

    assert problem_types(data) == ["unreachable"]
    assert data.problems[0]["severity"] == "error"
    # The texts stay the individual errors, as before.
    assert data.problem_reasons == ["down", "down", "down"]


async def test_since_stays_while_a_problem_lasts(hass, loaded_entry, fake_client):
    coordinator = loaded_entry.runtime_data
    fake_client.alerts_error = CrowdSecConnectionError("alerts down")
    first = await refresh(hass, coordinator)

    second = await refresh(hass, coordinator)

    assert second.problems[0]["since"] == first.problems[0]["since"]


async def test_the_parse_error_rate_is_a_warning(hass, config_entry, fake_client):
    coordinator = await setup(
        hass, config_entry, fake_client, **{CONF_PARSE_ERROR_THRESHOLD: 0.5}
    )

    (problem,) = coordinator.data.problems

    assert problem["type"] == "parse-errors"
    assert problem["severity"] == "warning"


async def test_no_log_lines_any_more_is_an_error(hass, config_entry, fake_client):
    lines = {"ok": 1000.0}

    def metrics():
        text = METRICS_TEXT.replace(
            'cs_parser_hits_ok_total{source="file"} 1000',
            f'cs_parser_hits_ok_total{{source="file"}} {lines["ok"]}',
        )
        return MetricSet(parse_prometheus(text))

    fake_client.metrics_fn = metrics
    coordinator = await setup(hass, config_entry, fake_client, no_lines_intervals=2)
    lines["ok"] += 10
    await refresh(hass, coordinator)

    # One quiet interval is tolerated, the second one is the problem.
    data = await refresh(hass, coordinator)
    assert "no-log-lines" not in problem_types(data)

    data = await refresh(hass, coordinator)

    assert "no-log-lines" in problem_types(data)
    assert (
        next(p for p in data.problems if p["type"] == "no-log-lines")["severity"]
        == "error"
    )


def _no_lines_problem(coordinator, lines_per_minute):
    """Evaluate one cycle with the given rate; the no-lines problem or None."""
    from custom_components.crowdsec.const import PROBLEM_NO_LOG_LINES
    from custom_components.crowdsec.coordinator import CrowdSecData

    data = CrowdSecData(reachable=True, lines_per_minute=lines_per_minute)
    coordinator._evaluate_problem(data)
    return next((p for p in data.problems if p["type"] == PROBLEM_NO_LOG_LINES), None)


async def test_a_single_quiet_interval_is_not_a_problem(
    hass, config_entry, fake_client
):
    """A home instance has minutes without a log line; that is not blindness."""
    coordinator = await setup(hass, config_entry, fake_client, no_lines_intervals=3)

    assert _no_lines_problem(coordinator, 1.0) is None
    assert _no_lines_problem(coordinator, 0.0) is None
    assert _no_lines_problem(coordinator, 0.0) is None
    problem = _no_lines_problem(coordinator, 0.0)
    assert problem is not None
    assert problem["severity"] == "error"
    assert "3 intervals" in problem["text"]


async def test_log_lines_reset_the_quiet_count(hass, config_entry, fake_client):
    coordinator = await setup(hass, config_entry, fake_client, no_lines_intervals=2)

    assert _no_lines_problem(coordinator, 1.0) is None
    assert _no_lines_problem(coordinator, 0.0) is None
    assert _no_lines_problem(coordinator, 2.0) is None
    assert _no_lines_problem(coordinator, 0.0) is None
    assert _no_lines_problem(coordinator, 0.0) is not None
    # Lines again end the problem at once.
    assert _no_lines_problem(coordinator, 0.5) is None


async def test_a_failed_scrape_neither_counts_nor_resets(
    hass, config_entry, fake_client
):
    coordinator = await setup(hass, config_entry, fake_client, no_lines_intervals=2)

    assert _no_lines_problem(coordinator, 1.0) is None
    assert _no_lines_problem(coordinator, 0.0) is None
    assert _no_lines_problem(coordinator, None) is None
    assert _no_lines_problem(coordinator, 0.0) is not None


async def test_no_lines_from_the_start_is_not_a_problem(
    hass, config_entry, fake_client
):
    """An instance that never saw a line may simply have nothing to read yet."""
    coordinator = await setup(hass, config_entry, fake_client, no_lines_intervals=1)

    for _ in range(5):
        assert _no_lines_problem(coordinator, 0.0) is None
