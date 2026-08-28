# Architecture

The integration is built around one rule: **data flows in one direction**, and
everything that can be tested without Home Assistant is kept out of the
framework's way.

```
api.py                     the only HTTP layer (Prometheus /metrics + LAPI)
  └── coordinator.py       one CrowdSecData per update cycle
        ├── metrics.py     Prometheus text → MetricSet
        ├── rates.py       counter deltas per minute
        ├── alerts.py      alert JSON → AlertSummary + rolling AlertCache
        ├── decisions.py   decisions + alerts → flat DecisionRecords
        └── timewindow.py  window splitting arithmetic
              └── CrowdSecData → sensor.py / binary_sensor.py
                              → websocket_api.py → the cards
                              → diagnostics.py
```

The middle block is deliberately free of Home Assistant imports. That is what
lets `tests/` run against it with nothing installed but pytest, and it is the
constraint to preserve when adding to it: anything that needs `hass` belongs in
the coordinator or above.

## Modules

| Path | Responsibility |
| --- | --- |
| `api.py` | The only HTTP layer. Two endpoints per instance: the Prometheus `/metrics` endpoint and the LAPI (`/v1/watchers/login`, `/v1/alerts`, `/v1/decisions`). Holds the LAPI JWT and renews it before expiry. Raises `CrowdSecAuthError` carrying the `ENDPOINT_*` that rejected — only `ENDPOINT_LAPI` means bad credentials and triggers reauth. |
| `coordinator.py` | A `DataUpdateCoordinator` producing one `CrowdSecData` dataclass per cycle. The three queries run through `asyncio.gather`, so their timeouts do not add up. Owns all cross-cycle state: `RateTracker` history, the `AlertCache`, seen alert IDs for ban events, the bouncer-idle counter, raw metrics for diagnostics. |
| `metrics.py` | Prometheus text parser → `MetricSet`. HA-free. |
| `rates.py` | Counter deltas per minute; discards the interval when `process_start_time_seconds` shows a restart. HA-free. |
| `alerts.py` | Alert JSON → `AlertSummary`, ban detection, alert IDs, plus the rolling `AlertCache`. HA-free. |
| `decisions.py` | Merges `/v1/decisions` with `/v1/alerts` into flat `DecisionRecord`s and parses Go durations. HA-free. |
| `timewindow.py` | Window splitting arithmetic for the truncation fallback. HA-free. |
| `validation.py` | Input checking (addresses, ban durations) shared by `services.py` and `websocket_api.py`. HA-free. |
| `entity.py` | `CrowdSecEntity` — binds every entity to the config entry's device. |
| `sensor.py`, `binary_sensor.py` | Description-driven platforms over `CrowdSecData`. |
| `websocket_api.py` | `crowdsec/decisions/list\|delete`, `crowdsec/instances`, `crowdsec/ip/lookup\|ban`. The cards read from here rather than from entity attributes — a ban table would blow past attribute size limits and land in the recorder. Admin-only; deletes are restricted to local-origin decisions. |
| `services.py`, `services.yaml` | `ban_ip`, `unban_ip`, `refresh`, all targeting a `config_entry_id`. |
| `config_flow.py` | Setup, reauth, reconfigure, options. `build_unique_id()` is shared with `async_migrate_entry` in `__init__.py` (v1 → v2 added the machine ID). |
| `repairs.py` | Repair flows. `api.py` only sets flags (e.g. `decisions_need_bouncer_key`) and the coordinator raises the issue — that is what keeps `api.py` HA-free. |
| `diagnostics.py` | Redacted dump of entry, raw metrics and last cycle. |
| `const.py` | Constants and `INTEGRATION_VERSION`, read from `manifest.json`. Never add a second version constant. |

## Data flow

1. The coordinator's cycle fires and runs the three queries concurrently.
2. Each query result is parsed by its HA-free module into a value object.
3. Cross-cycle state is folded in: rates need the previous counters, alerts the
   cache, ban events the set of seen IDs.
4. One `CrowdSecData` is published. Entities read it, the WebSocket commands
   read it (except the two `ip/*` ones, which go to the LAPI live), diagnostics
   dumps it.
5. Ban events (`crowdsec_new_ban`) are fired for newly seen alert IDs — silent
   on the first cycle, capped at `MAX_BAN_EVENTS_PER_CYCLE` (25), with the
   remainder deferred to later cycles rather than dropped.

**Availability is per query, not per cycle.** `CrowdSecData` carries
`metrics_ok`, `alerts_ok` and `decisions_ok`, and every sensor description names
its source through `source_fn`, so a sensor goes `unavailable` only when *its*
query failed. Letting a stuttering alert route blank the counters of a
successful metrics scrape put gaps into the recorder for data that was never in
doubt. `reachable` therefore means "the instance answered at all"; a single
failed route surfaces through `errors` and the `problem` flag. `last_update`
keeps the strict condition (`not errors`), because that is what an automation
compares against to spot stale values.

## Design decisions

- **Alerts are polled at two speeds.** Only every `alerts_full_interval`
  (default 300 s) does a cycle fetch the whole 24 h window; the others ask for
  the minutes since the last query and merge into the `AlertCache`. The
  aggregates are recomputed from the cache with the same `summarize_alerts`, so
  the evaluation is unchanged. `alerts_truncated` is remembered rather than
  recomputed: a truncated increment means alerts were missed, and only a full
  query can clear it.
- **No LAPI pagination.** `/v1/alerts` silently truncates at `limit`. The client
  halves the window and re-queries, up to `MAX_WINDOW_SPLITS` (4) levels deep;
  still truncated means a repair issue and `alerts_truncated`.
- **The ban table is local-only, always.** The LAPI's `origins` filter is
  honoured on some versions and ignored on others, so `build_table(local_only=True)`
  filters again on this side, over both sources. `active_decisions` therefore
  comes from the `cs_active_decisions` metric rather than the list length —
  counting a filtered list would silently drop CAPI and blocklist bans. The
  table is capped at `MAX_DECISION_ROWS`.
- **The lookup is not the table.** `crowdsec/ip/lookup` queries `/v1/decisions`
  with `ip=`/`range=` plus `contains=true`, which is what finds a *range*
  covering the address — the one thing the table structurally cannot show. It
  covers every origin: the table lists what can be acted on, the lookup answers
  whether an address is blocked at all.
- **The delete guard asks the LAPI, not the table.** A purely central address is
  absent from the local-only table, so a table-based check would never fire
  while the lookup card happily offers the unban. The by-id path keeps a table
  check as a safety net; the LAPI cannot look a decision up by id.
- **`/v1/decisions` returning 404 is normal** on some versions. Fallback chain:
  machine token → bouncer API key → the `cs_active_decisions` metric (count
  only, empty table).
- **The user agent is fixed.** CrowdSec parses it as the machine's version and
  requires exactly `name/version`; Home Assistant's composite UA causes a 401 on
  login. Hence `USER_AGENT` in `const.py`.
- **Card registration is per Home Assistant run**, not per entry
  (`CARD_REGISTERED` in `hass.data`). The static path is registered once and the
  JS URL carries `?v=<version>` for cache busting; a missing build only logs a
  warning, so everything except the card still works.

## The cards

Two elements in **one bundle**: `crowdsec-bans-card.ts` is the rollup entry
point and imports `ip-lookup-card.ts`, so both are defined by the single file
the integration serves. A third card means importing it there too, plus a
`window.customCards` entry.

| Path | Responsibility |
| --- | --- |
| `card/src/api.ts` | WebSocket calls, including the paging of `fetchAllDecisions`. |
| `card/src/filters.ts` | Search, filter and sort over the decision list. |
| `card/src/table.ts` | The decision table both cards render — same columns, cells and action column; each card keeps its own frame around it. |
| `card/src/styles.ts` | The visual vocabulary both cards share. A new card belongs on `sharedStyles`; writing its own set is how the two drifted apart the first time. |
| `card/src/localize.ts` | DE/EN strings. |
| `card/src/editor.ts`, `ip-lookup-editor.ts` | The visual editors. |

`filters.ts`, `localize.ts`, `api.ts` and `format.ts` are what vitest covers;
the elements themselves have no unit tests, since the card setup has no DOM
environment — `npm --prefix card run typecheck` is what guards them.

Rollup writes straight into `custom_components/crowdsec/www/`. The built file is
**not committed**: HACS installs get it from the release zip.
