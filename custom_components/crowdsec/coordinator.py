"""Update coordinator: fetches both endpoints and derives the metrics."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .alerts import (
    AlertCache,
    AlertSummary,
    new_bans,
    partition_bans,
    summarize_alerts,
)
from .api import (
    ENDPOINT_LAPI,
    AlertResult,
    CrowdSecAuthError,
    CrowdSecClient,
    CrowdSecConnectionError,
)
from .bouncers import (
    BouncerStatus,
    BouncerTracker,
    decision_requests_by_bouncer,
    route_is_decisions,
)
from .const import (
    ALERTS_INCREMENT_OVERLAP_MINUTES,
    ALERTS_SINCE,
    BOUNCER_UNIQUE_ID_INFIX,
    CONF_ALERTS_FULL_INTERVAL,
    CONF_ALERTS_LIMIT,
    CONF_BOUNCER_IDLE_INTERVALS,
    CONF_NO_LINES_INTERVALS,
    CONF_PARSE_ERROR_THRESHOLD,
    COUNTER_BOUNCER,
    COUNTER_LINES,
    COUNTER_OWN_DECISIONS,
    COUNTER_PARSE_KO,
    COUNTER_PARSE_OK,
    DEFAULT_ALERTS_FULL_INTERVAL,
    DEFAULT_ALERTS_LIMIT,
    DEFAULT_BOUNCER_IDLE_INTERVALS,
    DEFAULT_NO_LINES_INTERVALS,
    DEFAULT_PARSE_ERROR_THRESHOLD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NEW_BAN,
    ISSUE_ALERTS_TRUNCATED,
    ISSUE_DECISIONS_UNAVAILABLE,
    LOCAL_ORIGINS,
    MAX_DECISION_ROWS,
    METRIC_ACTIVE_DECISIONS,
    METRIC_BUCKETS,
    METRIC_INFO,
    METRIC_LAPI_BOUNCER_REQUESTS,
    METRIC_LAPI_DECISIONS_KO,
    METRIC_LAPI_DECISIONS_OK,
    METRIC_LAPI_MACHINE_REQUESTS,
    METRIC_LAPI_ROUTE_REQUESTS,
    METRIC_PARSER_HITS,
    METRIC_PARSER_KO,
    METRIC_PARSER_OK,
    METRIC_PREFIX,
    METRIC_PROCESS_START,
    METRIC_READER_HITS,
    PROBLEM_BOUNCER_IDLE,
    PROBLEM_NO_BOUNCER_QUERIES,
    PROBLEM_NO_LOG_LINES,
    PROBLEM_PARSE_ERRORS,
    PROBLEM_QUERY_FAILED,
    PROBLEM_UNREACHABLE,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    TOP_SCENARIO_COUNT,
)
from .decisions import DecisionRecord, build_table
from .metrics import MetricSet
from .problems import Problem, ProblemLog
from .rates import RateTracker, error_ratio
from .timewindow import parse_duration

_LOGGER = logging.getLogger(__name__)

# More ban events per cycle point to a catch-up (the instance was away for a
# long time, the alert window fills up all at once). In that case a single
# summary message is better than hundreds of events flooding the bus. The
# remainder is not dropped but carried over into the following cycles.
MAX_BAN_EVENTS_PER_CYCLE = 25


@dataclass(slots=True)
class CrowdSecData:
    """Everything the entities need from one update cycle."""

    # Whether the instance answered at all. The three queries are independent,
    # so this is *not* "everything worked" — one route refusing while the
    # others deliver says something about that route, not about the host being
    # gone. What failed is listed in ``errors`` and drives ``problem``.
    reachable: bool = False
    errors: list[str] = field(default_factory=list)
    problem: bool = False
    problem_reasons: list[str] = field(default_factory=list)
    # The same reasons with severity, type and start time (see problems.py).
    problems: list[dict[str, Any]] = field(default_factory=list)

    # One flag per query. Every value below belongs to exactly one of them, and
    # an entity goes unavailable when *its* source failed — not when any of the
    # three did. Otherwise an alert timeout would blank the counters of a
    # metrics scrape that ran perfectly well, tearing a hole into the
    # statistics for a route they do not depend on.
    metrics_ok: bool = False
    alerts_ok: bool = False

    scrape_duration: float | None = None
    last_restart: datetime | None = None
    last_update: datetime | None = None

    active_decisions: int | None = None
    decisions_by_reason: dict[str, float] = field(default_factory=dict)
    decisions_by_action: dict[str, float] = field(default_factory=dict)

    # The table behind the Lovelace card: active decisions enriched with the
    # alert details, plus the expired bans of the last 24 hours.
    decisions: list[DecisionRecord] = field(default_factory=list)
    # Whether the decision list itself could be read — the third query's flag.
    # Without it the card only has the history and has to say so. No sensor
    # hangs off this one; the table travels over the WebSocket.
    decisions_ok: bool = False
    # Set when the table hit MAX_DECISION_ROWS — the card says so rather than
    # pretending the list it shows is the whole picture.
    decisions_truncated: bool = False

    new_bans_24h: int | None = None
    alerts_24h: int | None = None
    alerts_truncated: bool = False
    unique_attackers_24h: int | None = None
    banned_attackers_24h: int | None = None
    last_alert: datetime | None = None

    top_scenario: str | None = None
    top_scenarios: list[dict[str, Any]] = field(default_factory=list)
    top_country: str | None = None
    top_countries: list[dict[str, Any]] = field(default_factory=list)
    top_attacker: str | None = None
    top_attackers: list[dict[str, Any]] = field(default_factory=list)

    active_buckets: int | None = None
    buckets_by_name: dict[str, float] = field(default_factory=dict)

    lines_total: float | None = None
    lines_per_minute: float | None = None
    parse_error_rate: float | None = None
    bouncer_queries_per_minute: float | None = None

    # Whether CrowdSec counts the requests per bouncer. Without that (at
    # ``prometheus.level: aggregated``) only the sum is known and ``bouncers``
    # stays empty.
    per_bouncer: bool = False
    # The known bouncers without the ignored ones, sorted by name.
    bouncers: list[BouncerStatus] = field(default_factory=list)

    version: str | None = None


class CrowdSecCoordinator(DataUpdateCoordinator[CrowdSecData]):
    """Polls a CrowdSec instance and keeps the counter history."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CrowdSecClient,
    ) -> None:
        options = entry.options
        interval = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=interval),
            config_entry=entry,
        )
        self.client = client
        self._rates = RateTracker()
        self._seen_traffic = False
        # Cycles in a row without a single log line. One is not enough to call
        # CrowdSec blind: a quiet home instance has those every few minutes.
        self._no_lines_cycles = 0
        self._bouncer_idle_cycles = 0
        # Whether the last scrape had the counters per bouncer. ``None`` until
        # the first one — the cycle then plays it safe (see
        # _async_query_metrics_and_decisions).
        self._per_bouncer: bool | None = None
        self._ignored_bouncers: set[str] = set()
        self._problem_log = ProblemLog()
        # Identifiers of the most recently seen alerts. ``None`` means
        # "nothing seen yet" — then no ban events are fired.
        self._known_alert_ids: set[str] | None = None
        self._device_version: str | None = None
        self._truncation_reported = False
        self._bouncer_key_reported = False
        # Raw CrowdSec metrics of the last successful scrape, only for the
        # diagnostics data.
        self.raw_metrics: dict[str, list[dict[str, Any]]] = {}
        self._parse_threshold = float(
            options.get(CONF_PARSE_ERROR_THRESHOLD, DEFAULT_PARSE_ERROR_THRESHOLD)
        )
        self._bouncer_idle_limit = int(
            options.get(CONF_BOUNCER_IDLE_INTERVALS, DEFAULT_BOUNCER_IDLE_INTERVALS)
        )
        self._no_lines_limit = int(
            options.get(CONF_NO_LINES_INTERVALS, DEFAULT_NO_LINES_INTERVALS)
        )
        self._bouncers = BouncerTracker(self._bouncer_idle_limit)
        self._alerts_limit = int(options.get(CONF_ALERTS_LIMIT, DEFAULT_ALERTS_LIMIT))
        self._alerts_full_interval = int(
            options.get(CONF_ALERTS_FULL_INTERVAL, DEFAULT_ALERTS_FULL_INTERVAL)
        )
        # The rolling alert window. It is filled by a full query now and then
        # and topped up every cycle; see AlertCache for why.
        window = parse_duration(ALERTS_SINCE) or 24 * 60
        self._alert_cache = AlertCache(timedelta(minutes=window))
        self._alerts_full_at: float | None = None
        self._alerts_polled_at: float | None = None
        self._force_full_alerts = False
        self._alerts_truncated = False

    @property
    def decisions_origins(self) -> tuple[str, ...]:
        """The origins the decision query is restricted to.

        Always the local ones. The table exists to show what this Home
        Assistant can act on; whether some address is blocked by the CAPI or a
        blocklist is the lookup card's question, and it asks the LAPI directly
        rather than going through this list.
        """
        return LOCAL_ORIGINS

    def request_full_alert_poll(self) -> None:
        """Make the next cycle fetch the whole alert window again.

        A manual refresh should not hand back whatever the incremental query
        happened to see — someone pressing the button wants the current
        picture, not a partially aged one.
        """
        self._force_full_alerts = True

    # -- Update cycle -----------------------------------------------------

    async def _async_update_data(self) -> CrowdSecData:
        started = monotonic()
        previous = self.data
        data = CrowdSecData()

        self._sync_bouncer_registry()

        # The three queries do not depend on each other. Run one after another,
        # three timeouts add up in the worst case — in parallel the cycle stays
        # within a single one.
        full_alerts = self._alerts_due_in_full(started)
        (
            (metrics_result, own_requests, decisions_result),
            alerts_result,
        ) = await asyncio.gather(
            # Never raises: its exceptions come back as results.
            self._async_query_metrics_and_decisions(),
            self._async_query_alerts(started, full_alerts),
            return_exceptions=True,
        )

        metrics = self._unwrap(data, metrics_result, MetricSet)
        alerts = self._unwrap(data, alerts_result, AlertResult)
        raw_decisions = self._unwrap(data, decisions_result, list)

        data.scrape_duration = round(monotonic() - started, 3)
        # Both queries always produce a result object; ``None`` only ever comes
        # out of _unwrap when the call failed.
        data.metrics_ok = metrics is not None
        data.alerts_ok = alerts is not None

        if metrics is not None:
            self.raw_metrics = metrics.as_dict(METRIC_PREFIX)
            self._apply_metrics(data, metrics, own_requests)
        else:
            # Without fresh counters every rate comparison is worthless.
            self._rates.reset()
            self._bouncers.reset()
            # What is known about the bouncers stays, so a stopped one is not
            # dropped from the problems just because one scrape failed. Their
            # entities go unavailable all the same: their source is down.
            data.per_bouncer = bool(self._per_bouncer)
            if data.per_bouncer:
                data.bouncers = self._bouncers.statuses(self._ignored_bouncers)

        if alerts is not None:
            self._absorb_alerts(alerts, full_alerts, started)
            self._apply_alerts(data)

        self._apply_decisions(
            data,
            raw_decisions,
            self._alert_cache.alerts,
            previous,
        )

        # The host answered as long as any one of the three came back. A single
        # route refusing is a problem, not an outage — that distinction is the
        # whole reason the flags above exist.
        data.reachable = data.metrics_ok or data.alerts_ok or data.decisions_ok

        if not data.errors:
            # Deliberately the strict condition: "last update" is what an
            # automation compares against to spot stale values, and a cycle
            # that lost one of its three queries did not fully update.
            data.last_update = datetime.now(UTC)
        else:
            data.last_update = previous.last_update if previous else None

        if previous is not None:
            # These two describe the past, not the current state, so a failed
            # query carries the old value over rather than blanking it — but
            # only the query that actually failed. With alerts intact, an empty
            # window genuinely means "no alert in the last 24 hours" and has to
            # be allowed to clear the timestamp.
            if not data.metrics_ok and data.last_restart is None:
                data.last_restart = previous.last_restart
            if not data.alerts_ok and data.last_alert is None:
                data.last_alert = previous.last_alert

        self._evaluate_problem(data)
        self._update_device_version(data)
        self._report_missing_bouncer_key()
        return data

    async def _async_query_metrics_and_decisions(
        self,
    ) -> tuple[Any, tuple[int, int], Any]:
        """Scrape the metrics and fetch the decision list.

        Normally both run side by side. Where CrowdSec only counts the
        requests of all bouncers together, the integration's own decision
        query is part of that sum and has to be subtracted — which only works
        if it is clear on which side of the scrape it landed. Then the
        decision query follows the scrape, and the own requests are read in
        between.

        Exceptions come back as results, as with ``asyncio.gather``.
        """
        decisions = self.client.async_get_decisions(self.decisions_origins)
        if self._per_bouncer:
            metrics, result = await asyncio.gather(
                self.client.async_get_metrics(), decisions, return_exceptions=True
            )
            return metrics, self._own_decision_requests(), result

        (metrics,) = await asyncio.gather(
            self.client.async_get_metrics(), return_exceptions=True
        )
        own = self._own_decision_requests()
        (result,) = await asyncio.gather(decisions, return_exceptions=True)
        return metrics, own, result

    def _own_decision_requests(self) -> tuple[int, int]:
        """All own requests to the decision routes, and those with the key."""
        return (
            self.client.decision_requests,
            self.client.bouncer_decision_requests,
        )

    def _sync_bouncer_registry(self) -> None:
        """Read the known and the ignored bouncers off the entity registry.

        Every bouncer has a binary sensor. Its registry entry is what keeps the
        bouncer known across restarts, and disabling it is how a bouncer is
        ignored — no option of its own needed.
        """
        entry = self.config_entry
        if entry is None:
            return
        prefix = f"{entry.entry_id}{BOUNCER_UNIQUE_ID_INFIX}"
        names: set[str] = set()
        ignored: set[str] = set()
        registry = er.async_get(self.hass)
        for item in er.async_entries_for_config_entry(registry, entry.entry_id):
            if item.domain != "binary_sensor" or not item.unique_id.startswith(prefix):
                continue
            name = item.unique_id[len(prefix) :]
            names.add(name)
            if item.disabled_by is not None:
                ignored.add(name)
        self._bouncers.sync_registry(names)
        self._ignored_bouncers = ignored

    def bouncer_entity_added(self, name: str) -> None:
        """A bouncer's entity was added — it is in the registry from now on."""
        self._bouncers.mark_registered(name)

    def _unwrap(self, data: CrowdSecData, result: Any, expected: type) -> Any | None:
        """Evaluate one result from ``asyncio.gather``.

        Expected errors end up as a message in the data (or trigger a reauth);
        everything else is passed on so that it does not silently pass as
        "not reachable".
        """
        if isinstance(result, CrowdSecAuthError):
            self._handle_auth_error(data, result)
            return None
        if isinstance(result, CrowdSecConnectionError):
            data.errors.append(str(result))
            return None
        if isinstance(result, BaseException):
            raise result
        if result is None or isinstance(result, expected):
            return result
        return None

    def _handle_auth_error(self, data: CrowdSecData, err: CrowdSecAuthError) -> None:
        """Decide whether an access error has to block the entry.

        Only a rejected login means "wrong credentials" and justifies a reauth
        dialog. If a single route refuses despite a valid token, the remaining
        entities keep running — the outage is listed in the problem flag.
        """
        if err.endpoint == ENDPOINT_LAPI:
            raise ConfigEntryAuthFailed(str(err)) from err
        _LOGGER.warning("Access to %s denied: %s", err.endpoint, err)
        data.errors.append(str(err))

    # -- Evaluation -------------------------------------------------------

    def _apply_metrics(
        self, data: CrowdSecData, metrics: MetricSet, own_requests: tuple[int, int]
    ) -> None:
        data.version = metrics.label_of(METRIC_INFO, "version")

        start_time = metrics.single(METRIC_PROCESS_START)
        if start_time:
            data.last_restart = datetime.fromtimestamp(start_time, tz=UTC)

        active = metrics.total(METRIC_ACTIVE_DECISIONS)
        if active is not None:
            data.active_decisions = int(active)
            data.decisions_by_reason = metrics.group_sum(
                METRIC_ACTIVE_DECISIONS, "reason"
            )
            data.decisions_by_action = metrics.group_sum(
                METRIC_ACTIVE_DECISIONS, "action"
            )

        buckets = metrics.total(METRIC_BUCKETS)
        if buckets is not None:
            data.active_buckets = int(buckets)
            data.buckets_by_name = {
                name: value
                for name, value in metrics.group_sum(METRIC_BUCKETS, "name").items()
                if value > 0
            }

        parse_ok = metrics.total(METRIC_PARSER_OK)
        parse_ko = metrics.total(METRIC_PARSER_KO)
        lines = metrics.first_total((METRIC_PARSER_HITS, METRIC_READER_HITS))
        if lines is None and (parse_ok is not None or parse_ko is not None):
            # A missing ok/ko counter counts as 0 — CrowdSec only exports the
            # ko metric after the first parse error.
            lines = (parse_ok or 0.0) + (parse_ko or 0.0)
        data.lines_total = lines

        counters: dict[str, float] = {}
        if lines is not None:
            counters[COUNTER_LINES] = lines
        if parse_ok is not None:
            counters[COUNTER_PARSE_OK] = parse_ok
        if parse_ko is not None:
            counters[COUNTER_PARSE_KO] = parse_ko

        # The integration logs in as a machine every cycle, so the machine
        # counter is there whenever CrowdSec counts per client at all — even
        # right after a restart, before any bouncer has asked for anything.
        per_bouncer = (
            METRIC_LAPI_BOUNCER_REQUESTS in metrics
            or METRIC_LAPI_MACHINE_REQUESTS in metrics
        )
        self._per_bouncer = per_bouncer
        data.per_bouncer = per_bouncer
        bouncers_evaluated = False
        if per_bouncer:
            bouncers_evaluated = self._bouncers.update(
                decision_requests_by_bouncer(metrics, METRIC_LAPI_BOUNCER_REQUESTS),
                start_time,
                monotonic(),
                datetime.now(UTC),
            )
            data.bouncers = self._bouncers.statuses(self._ignored_bouncers)
            if bouncers_evaluated:
                data.bouncer_queries_per_minute = sum(
                    status.queries_per_minute or 0.0 for status in data.bouncers
                )
        else:
            self._bouncers.reset()
            self._add_summed_bouncer_counters(counters, metrics, own_requests)

        window = self._rates.update(counters, start_time, monotonic())
        if window is None:
            # First cycle or restart: "unknown" is better than a jump.
            data.parse_error_rate = error_ratio(parse_ok, parse_ko)
            return

        data.lines_per_minute = window.per_minute(COUNTER_LINES)
        if not per_bouncer:
            requests = window.deltas.get(COUNTER_BOUNCER)
            if requests is not None and window.elapsed > 0:
                own = window.deltas.get(COUNTER_OWN_DECISIONS, 0.0)
                data.bouncer_queries_per_minute = (
                    max(0.0, requests - own) / window.elapsed * 60.0
                )

        interval_rate = error_ratio(
            window.deltas.get(COUNTER_PARSE_OK), window.deltas.get(COUNTER_PARSE_KO)
        )
        # Without lines in the interval there is no interval ratio — the sensor
        # then shows the ratio over the whole runtime.
        data.parse_error_rate = (
            interval_rate
            if interval_rate is not None
            else error_ratio(parse_ok, parse_ko)
        )

    def _add_summed_bouncer_counters(
        self,
        counters: dict[str, float],
        metrics: MetricSet,
        own_requests: tuple[int, int],
    ) -> None:
        """The fallback without counters per bouncer: one sum for all.

        The sum contains the integration's own decision queries, so their
        count goes along and is subtracted from the increase. Both are kept as
        counters of their own — their difference could step backwards and
        make the rate tracker throw the whole interval away.
        """
        own_all, own_with_key = own_requests
        routes = metrics.total(METRIC_LAPI_ROUTE_REQUESTS, route_is_decisions)
        if routes is not None:
            counters[COUNTER_BOUNCER] = routes
            counters[COUNTER_OWN_DECISIONS] = float(own_all)
            return
        # Older still: only the ok/ko counters of the decision route, which see
        # nothing but requests made with a bouncer key.
        ok = metrics.total(METRIC_LAPI_DECISIONS_OK) or 0.0
        ko = metrics.total(METRIC_LAPI_DECISIONS_KO)
        if ko is not None:
            counters[COUNTER_BOUNCER] = ok + ko
            counters[COUNTER_OWN_DECISIONS] = float(own_with_key)

    # -- Alerts -----------------------------------------------------------

    def _alerts_due_in_full(self, now: float) -> bool:
        """Whether this cycle has to fetch the whole window again."""
        if self._force_full_alerts or self._alerts_full_at is None:
            return True
        return now - self._alerts_full_at >= self._alerts_full_interval

    async def _async_query_alerts(self, now: float, full: bool) -> AlertResult:
        """One alert query — either the whole window or only what is new.

        The incremental window is measured from the last *successful* query, so
        a failed cycle does not tear a hole into the series: the next one
        simply asks for a longer stretch.
        """
        if full or self._alerts_polled_at is None:
            return await self.client.async_get_alerts(
                since=ALERTS_SINCE, limit=self._alerts_limit
            )

        elapsed = max(0.0, now - self._alerts_polled_at)
        minutes = int(elapsed // 60) + ALERTS_INCREMENT_OVERLAP_MINUTES
        return await self.client.async_get_alerts(
            since=f"{minutes}m", limit=self._alerts_limit
        )

    def _absorb_alerts(self, result: AlertResult, full: bool, now: float) -> None:
        """Merge the result of a query into the rolling window."""
        if full:
            self._alert_cache.replace(result.alerts)
            self._alerts_full_at = now
            self._force_full_alerts = False
            # A full query re-establishes the truth about completeness; an
            # earlier incremental truncation is no longer relevant.
            self._alerts_truncated = result.truncated
        else:
            self._alert_cache.add(result.alerts)
            # A truncated increment means alerts were missed, and the cache
            # cannot heal that by itself — the flag stays until the next full
            # query proves otherwise.
            self._alerts_truncated = self._alerts_truncated or result.truncated

        self._alerts_polled_at = now
        self._alert_cache.prune(datetime.now(UTC))

    def _apply_alerts(self, data: CrowdSecData) -> None:
        """Derive the 24h numbers from the rolling window."""
        summary = summarize_alerts(self._alert_cache.alerts, TOP_SCENARIO_COUNT)

        data.alerts_truncated = self._alerts_truncated
        data.alerts_24h = summary.alerts
        data.new_bans_24h = summary.ban_decisions
        data.unique_attackers_24h = summary.unique_sources
        data.banned_attackers_24h = summary.banned_sources
        data.last_alert = summary.latest_alert

        data.top_scenarios = summary.top_scenarios
        data.top_scenario = summary.top_scenario
        data.top_countries = summary.top_countries
        data.top_country = summary.top_country
        data.top_attackers = summary.top_sources
        data.top_attacker = summary.top_source

        deferred = self._fire_ban_events(summary)
        # Bans held back by the per-cycle cap must not count as known, otherwise
        # ``new_bans`` would filter them out for good and their event would
        # never be fired.
        self._known_alert_ids = summary.seen_ids - deferred
        self._report_truncation(self._alerts_truncated)

    def _apply_decisions(
        self,
        data: CrowdSecData,
        raw_decisions: list[Any] | None,
        alerts: list[dict[str, Any]],
        previous: CrowdSecData | None,
    ) -> None:
        """Build the table for the card.

        Only local decisions end up here, so the row count is deliberately not
        the number of active decisions — the CAPI and the blocklists are
        missing from it by design. ``active_decisions`` therefore stays with
        the ``cs_active_decisions`` metric, which counts everything the LAPI
        enforces.
        """
        if raw_decisions is None:
            data.decisions_ok = False
            # A failed decision query must not empty the table — the card would
            # then show "no bans" for an instance that is merely unreachable.
            data.decisions = previous.decisions if previous else []
            data.decisions_truncated = (
                previous.decisions_truncated if previous else False
            )
            return

        data.decisions_ok = True
        # The LAPI honours the origins filter on some versions and ignores it
        # on others, so the table filters again here. Without that a blocklist
        # entry could still surface in a card that promises local decisions.
        rows = build_table(raw_decisions, alerts, local_only=True)
        data.decisions_truncated = len(rows) > MAX_DECISION_ROWS
        if data.decisions_truncated:
            _LOGGER.debug(
                "Decision table has %d rows, showing the first %d",
                len(rows),
                MAX_DECISION_ROWS,
            )
        # build_table sorts active first and by remaining time, so the cut
        # takes away the rows furthest in the past, not a random selection.
        data.decisions = rows[:MAX_DECISION_ROWS]

    def _fire_ban_events(self, summary: AlertSummary) -> set[str]:
        """Fire one event per newly detected ban.

        Returns the identifiers of the bans that the cap held back. They stay
        unknown and are therefore picked up again in one of the next cycles.
        """
        fresh = new_bans(summary, self._known_alert_ids)
        if not fresh:
            return set()

        total = len(fresh)
        fresh, deferred = partition_bans(fresh, MAX_BAN_EVENTS_PER_CYCLE)
        if deferred:
            _LOGGER.info(
                "%d new bans in one cycle — %d are reported now, %d follow in "
                "the next cycles",
                total,
                len(fresh),
                len(deferred),
            )

        entry_id = self.config_entry.entry_id if self.config_entry else None
        for ban in fresh:
            self.hass.bus.async_fire(
                EVENT_NEW_BAN,
                {"entry_id": entry_id, "instance": self.name, **ban.as_event_data()},
            )

        return deferred

    def _report_truncation(self, truncated: bool) -> None:
        """Create a repair issue when the 24h numbers are incomplete."""
        if truncated == self._truncation_reported:
            return
        self._truncation_reported = truncated
        entry = self.config_entry
        if entry is None:
            return

        issue_id = f"{ISSUE_ALERTS_TRUNCATED}_{entry.entry_id}"
        if not truncated:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_ALERTS_TRUNCATED,
            translation_placeholders={
                "name": entry.title,
                "limit": str(self._alerts_limit),
            },
        )

    def _report_missing_bouncer_key(self) -> None:
        """Offer a repair when the LAPI will not hand over the decision list.

        Some CrowdSec versions only serve ``/v1/decisions`` to bouncers. The
        user sees an empty ban table and, so far, nothing but a warning in the
        log to explain it — with no hint that a bouncer key is all it takes.
        """
        needed = getattr(self.client, "decisions_need_bouncer_key", False)
        if needed == self._bouncer_key_reported:
            return
        self._bouncer_key_reported = needed
        entry = self.config_entry
        if entry is None:
            return

        issue_id = f"{ISSUE_DECISIONS_UNAVAILABLE}_{entry.entry_id}"
        if not needed:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_DECISIONS_UNAVAILABLE,
            translation_placeholders={"name": entry.title},
            data={"entry_id": entry.entry_id},
        )

    def _update_device_version(self, data: CrowdSecData) -> None:
        """Keep the firmware information of the device up to date.

        The version sits in ``cs_info`` and changes when CrowdSec is updated.
        Without this comparison the device registry would forever show the
        version that applied at the first start.
        """
        if data.version is None or data.version == self._device_version:
            return
        entry = self.config_entry
        if entry is None:
            return

        registry = dr.async_get(self.hass)
        identifier = (DOMAIN, entry.entry_id)
        # async_get_device is deprecated and stops working in Home Assistant
        # 2027.8. Its successor does not exist in the older versions this
        # integration still supports; the fallback goes once the minimum
        # version in hacs.json has it.
        if hasattr(registry, "async_get_device_by_identifier"):
            device = registry.async_get_device_by_identifier(identifier, entry.entry_id)
        else:
            device = registry.async_get_device(identifiers={identifier})
        if device is None:
            # The entities have not been created yet — they will fill in the
            # version themselves anyway.
            self._device_version = data.version
            return
        if device.sw_version != data.version:
            registry.async_update_device(device.id, sw_version=data.version)
        self._device_version = data.version

    def _evaluate_problem(self, data: CrowdSecData) -> None:
        """Set the aggregate flag for automations.

        Every problem goes into ``problems`` with its severity and into
        ``problem_reasons`` as text — the latter unchanged in shape, so the
        automations built on it keep working.
        """
        problems: list[Problem] = []

        # Unconditionally: now that ``reachable`` only says the instance
        # answered at all, this flag is the one place a partial outage still
        # surfaces. Tying it to ``reachable`` would let a permanently broken
        # alert route pass unnoticed as long as the metrics kept coming.
        if not data.reachable:
            detail = "; ".join(data.errors)
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    PROBLEM_UNREACHABLE,
                    f"Instance not reachable: {detail}"
                    if detail
                    else "Instance not reachable",
                )
            )
        else:
            problems.extend(
                Problem(SEVERITY_WARNING, PROBLEM_QUERY_FAILED, error)
                for error in data.errors
            )

        if (
            data.parse_error_rate is not None
            and data.parse_error_rate > self._parse_threshold
        ):
            problems.append(
                Problem(
                    SEVERITY_WARNING,
                    PROBLEM_PARSE_ERRORS,
                    f"Parse error rate {data.parse_error_rate:.1f} % above "
                    f"threshold {self._parse_threshold:.1f} %",
                )
            )

        # A failed scrape (None) neither counts as silence nor breaks it — the
        # failed query is a problem of its own.
        if data.lines_per_minute is not None:
            if data.lines_per_minute > 0:
                self._seen_traffic = True
                self._no_lines_cycles = 0
            elif self._seen_traffic:
                self._no_lines_cycles += 1
                if self._no_lines_cycles >= self._no_lines_limit:
                    problems.append(
                        Problem(
                            SEVERITY_ERROR,
                            PROBLEM_NO_LOG_LINES,
                            f"No log lines processed for {self._no_lines_cycles} "
                            "intervals — CrowdSec sees nothing",
                        )
                    )

        problems.extend(self._bouncer_problems(data))

        # The texts as before: the individual errors first, not one sentence
        # summing them up.
        reasons = [*data.errors] + [
            problem.text
            for problem in problems
            if problem.type not in (PROBLEM_UNREACHABLE, PROBLEM_QUERY_FAILED)
        ]
        if not data.reachable and not data.errors:
            reasons.insert(0, problems[0].text)

        data.problems = self._problem_log.update(problems, datetime.now(UTC))
        data.problem_reasons = reasons
        data.problem = bool(problems)

    def _bouncer_problems(self, data: CrowdSecData) -> list[Problem]:
        """A stopped bouncer, or no bouncer queries at all.

        Per bouncer where CrowdSec counts per bouncer. The sum over all of them
        only decides where it does not, or when no bouncer is known — then
        nobody is enforcing anything. Bouncers that are known but all ignored
        are a choice, not a problem.
        """
        if data.bouncer_queries_per_minute is not None:
            if data.bouncer_queries_per_minute > 0:
                self._bouncer_idle_cycles = 0
            else:
                self._bouncer_idle_cycles += 1

        if data.per_bouncer and self._bouncers.names:
            return [
                Problem(
                    SEVERITY_ERROR,
                    PROBLEM_BOUNCER_IDLE,
                    f"Bouncer {status.name}: no queries for {status.idle_cycles} "
                    "intervals — its decisions are not being enforced",
                    bouncer=status.name,
                )
                for status in data.bouncers
                if status.active is False
            ]

        if self._bouncer_idle_cycles >= self._bouncer_idle_limit:
            return [
                Problem(
                    SEVERITY_ERROR,
                    PROBLEM_NO_BOUNCER_QUERIES,
                    f"No bouncer queries for {self._bouncer_idle_cycles} "
                    "intervals — decisions are not being enforced",
                )
            ]
        return []


# Typed entry: the coordinator lives in entry.runtime_data.
CrowdSecConfigEntry = ConfigEntry[CrowdSecCoordinator]
