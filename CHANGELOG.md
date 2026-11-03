# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

An entry is put together by the release workflow: the text written by hand in
`.release/next.md`, followed by the list of commits. Installation instructions
are added to the release page and do not belong in an entry. The entries up to
1.3.5 were written by hand as a whole.

## [1.5.0](https://github.com/stefgo/ha-crowdsec-integration/compare/v1.4.0...v1.5.0) (2026-11-03)

### Fixed

- **No more "API key not found" errors in the CrowdSec log.** The integration
  asked for the decision list with its machine credentials first, which
  CrowdSec always refuses, and logged in again on every poll before falling
  back to the bouncer key. It now asks with the bouncer key straight away; the
  log stays clean and the needless login is gone.
- **Removing all decisions for an address works.** CrowdSec refused the
  request behind the `crowdsec.unban_ip` service and behind *Remove all
  decisions for …* in both cards, so nothing was removed. The service now
  removes every decision for the address, as documented. The cards remove only
  the address's own local decisions, so a decision from the CAPI or a blocklist
  for the same address, or a range covering it, stays in place. The lookup card
  offers the button only when a local decision names the address itself; a
  local range covering it is removed through its own row.
- **Expired bans in the ban table show when they really ran out.** The
  expiry was off by the age of the alert, so a ban from the morning could
  appear to have ended before it even started, and the expired rows were
  sorted accordingly.
- **The ban table shows the effect of every ban and unban at once.** A second
  click within a few seconds of the first used to come back with the row it
  had just removed.
- **Repair hints go away once their cause is fixed.** A hint about a missing
  bouncer key or incomplete alert numbers stayed in Repairs after the key was
  added through *Reconfigure* or the limit was raised in the options, until
  Home Assistant restarted. Removing an instance now also removes its hints.
- **An empty ban list is shown as empty.** Some CrowdSec versions answer "no
  decisions" with *not found*. The card took that for an error, kept the last
  table on screen and said the list was unavailable.
- **Busy instances no longer lose alerts at the seams of the 24-hour window.**
  When more alerts arrive than one query returns, the window is fetched in
  parts; alerts that started between two of those parts were missing from the
  counts.
- **Checking a bouncer key no longer downloads every decision.** Setting up an
  instance or adding a key through the repair fetched the whole decision list
  just to see whether the key works — hundreds of thousands of entries with a
  blocklist subscribed. It now asks about a single address.
- **Diagnostics leave out the machine name and the host everywhere.** Both
  were already hidden in the configuration section but still showed up in the
  raw metrics and in the error messages.
- **The cards say what is wrong in your language.** A mistyped address in the
  lookup card was answered with "No decision was selected". It now says the
  address or range is not valid, and an invalid ban duration or a missing
  bouncer key get a message of their own instead of an English one.

### Changed

- **Every bouncer is a device of its own, and a removed bouncer can be
  deleted.** A bouncer taken out of CrowdSec (`cscli bouncers delete` or
  `prune`) used to keep its entity, which could only be removed after
  disabling it first, and was reported as stopped again after a few
  intervals. Each bouncer now shows up as
  a device connected via its CrowdSec instance; *Delete* on that device page
  forgets it. A bouncer that still queries cannot be deleted — disable it to
  ignore it instead.
- **A banned range is stored as a range.** Banning a CIDR range such as
  `192.0.2.0/24` from Home Assistant now gives the decision the scope `Range`,
  the way `cscli decisions add --range` does, instead of `Ip`. Bans set before
  the update keep their old scope until they expire.
- **A malformed ban duration is refused right away.** Values such as `1h30`
  (the last number has no unit) used to pass the check and then fail at
  CrowdSec with a less helpful message. Write `1h30m` instead.

### Upgrade notes

- **The ban table and the IP lookup need a bouncer API key.** CrowdSec hands
  the decision list to bouncers only — that was always the case, the
  integration just tried the machine credentials first. Without a key nothing
  is asked any more and Home Assistant offers a repair to add one
  (`cscli bouncers add homeassistant`). The `Active decisions` sensor works
  without a key as before.
- **Bouncers moved to devices of their own.** Their entity IDs, names, history
  and settings stay as they are. Anything that refers to the CrowdSec
  *device* — device triggers and conditions, actions aimed at the device, an
  area assigned through the device — no longer includes the bouncers; give the
  bouncer devices an area of their own if needed. Going back to an older
  version leaves the bouncer devices behind empty; they do no harm.

### Features

* Give every bouncer a device of its own that can be deleted ([80d595f](https://github.com/stefgo/ha-crowdsec-integration/commit/80d595ff04638fe54b1ce47e89c7cf3bd8421f52)), closes [#34](https://github.com/stefgo/ha-crowdsec-integration/issues/34)

### Bug Fixes

* Ban a CIDR range with the Range scope ([e934b8c](https://github.com/stefgo/ha-crowdsec-integration/commit/e934b8c34634266685f24d3627bf0d1c374dabc5))
* Clear repair issues after a reload and with the entry ([8b1d547](https://github.com/stefgo/ha-crowdsec-integration/commit/8b1d5470ef7755cd14dc511e0517478218343af2))
* Count the expiry of past bans from the fetch, not the alert ([2e9e0d1](https://github.com/stefgo/ha-crowdsec-integration/commit/2e9e0d1e702c76ec3970ed24ea4ac402154b488b))
* Give every error of the cards a fitting text in both languages ([e637406](https://github.com/stefgo/ha-crowdsec-integration/commit/e637406038c131942827e171187c04de70d2bd14))
* Keep the machine ID and the hosts out of every part of diagnostics ([c464945](https://github.com/stefgo/ha-crowdsec-integration/commit/c4649458295e713f814888582cd58223a5939add))
* Offer "remove all for this address" only where it removes something ([0e0f791](https://github.com/stefgo/ha-crowdsec-integration/commit/0e0f791bb0ad40e798cdcaba6232b6b51b8c4182))
* Overlap the parts of a split alert window by a minute ([5cf646b](https://github.com/stefgo/ha-crowdsec-integration/commit/5cf646b65d046718c5d993bb92cfa99e1f70c579))
* Re-read the ban table right after a ban or an unban ([7e23639](https://github.com/stefgo/ha-crowdsec-integration/commit/7e236399c6780075c1d8450549b420e8ae72545c))
* Read a 404 from the decision list as an empty list ([e6744ae](https://github.com/stefgo/ha-crowdsec-integration/commit/e6744ae1dba7b5ea3d0a9249ca1e4a2f4f1a06a5))
* Read the decision list with the bouncer key only ([b75dd8c](https://github.com/stefgo/ha-crowdsec-integration/commit/b75dd8caf540351017aa8e9ddb139586dc441859)), closes [#33](https://github.com/stefgo/ha-crowdsec-integration/issues/33)
* Refuse a ban duration that is not a whole Go duration ([48e21af](https://github.com/stefgo/ha-crowdsec-integration/commit/48e21af91106611c53cb1ebd2ded597750151f78))
* Refuse an unban by address the guard cannot check ([32dc5a6](https://github.com/stefgo/ha-crowdsec-integration/commit/32dc5a6f5286b4d92ad949c6e26edc16f008c64c))
* Remove the decisions of an address by a filter CrowdSec accepts ([d496d21](https://github.com/stefgo/ha-crowdsec-integration/commit/d496d211f5590edb81ae9a9979253f1b55618415))

### Performance

* Check the bouncer key with one address instead of the whole list ([bdca7f4](https://github.com/stefgo/ha-crowdsec-integration/commit/bdca7f4b8e6977a6554c6507200a60d1a3d62e4e))

## [1.4.0](https://github.com/stefgo/ha-crowdsec-integration/compare/v1.3.6...v1.4.0) (2026-10-31)

### Each bouncer is watched on its own

A bouncer is what actually blocks: CrowdSec decides, the bouncer enforces. If it
stops, nothing is blocked any more and nothing looks wrong. Until now the
integration only knew the requests of all bouncers together, and that sum
included its own decision query — so the "no bouncer queries" problem could
never trigger while Home Assistant was running, and one stopped bouncer was
hidden by any other still running.

- **New: `Bouncer <name>`** — one entity per bouncer, on while it fetches
  decisions, off once it has not done so for the configured number of intervals.
  Bouncers appear by themselves and stay known across restarts.
- **New: `Active bouncers`** — how many bouncers are fetching decisions, with
  the list as attribute.
- **`Status`** names the bouncer that stopped.
- **To ignore a bouncer, disable its entity** — for a bouncer that is gone, or
  for the key of the integration itself if it shows up as `homeassistant`.
- `Bouncer queries per minute` now counts the bouncers only and is about one
  query per minute lower than before. With `prometheus.level: aggregated`,
  where CrowdSec counts no bouncers individually, it is the sum without the
  integration's own queries, as before but corrected.

### Problems with a severity

`Status` has a new attribute `problems`: every reason with `severity` (error or
warning), a fixed `type`, the text and `since`. Templates and automations can
tell errors from warnings without matching the wording. `reasons` stays as it
was.

### Fixes

- **`New bans 24h` and the Top sensors count only what this instance detected.**
  Until now every blocklist and CAPI refresh was counted as well, so an instance with
  subscribed blocklists showed tens of thousands of bans it never made, and
  `Top scenario 24h` read `update : +15000/-0 IPs`. The same refreshes also fired a
  `crowdsec_new_ban` event without an IP; that no longer happens. Expect the numbers
  to drop sharply after the upgrade — they now match the ban table.
- **`Status` no longer flaps on quiet instances.** "No log lines processed" used to
  trigger after a single interval without log lines. It now waits for the new option
  **Intervals without log lines before a problem** (default 30, which is 30 minutes at
  the default poll interval). Raise it for an instance that is quiet for longer
  stretches, lower it to hear about a blind CrowdSec sooner.
- **Ready for Home Assistant 2027.8.** The integration no longer uses a device
  registry call that Home Assistant removes in 2027.8 and warns about until then.

### Features

* Watch each bouncer on its own and report problems with a severity ([f83657c](https://github.com/stefgo/ha-crowdsec-integration/commit/f83657cc095f8027ad366a0e9c190242feb205b9))

### Bug Fixes

* Leave blocklist and CAPI alerts out of the 24h numbers ([dec5932](https://github.com/stefgo/ha-crowdsec-integration/commit/dec59324522bcc44539cbfb747922474f9f3ba65)), closes [#23](https://github.com/stefgo/ha-crowdsec-integration/issues/23)
* Look up the device by identifier for Home Assistant 2027.8 ([529086b](https://github.com/stefgo/ha-crowdsec-integration/commit/529086b66e4ddba359392f216af4ab5fb54a25bd)), closes [#26](https://github.com/stefgo/ha-crowdsec-integration/issues/26)
* Tolerate quiet intervals before reporting missing log lines ([ed6d5f0](https://github.com/stefgo/ha-crowdsec-integration/commit/ed6d5f0f258f0bb470fe24985ff8816d181b5552)), closes [#25](https://github.com/stefgo/ha-crowdsec-integration/issues/25)

## [1.3.6](https://github.com/stefgo/ha-crowdsec-integration/compare/v1.3.5...v1.3.6) (2026-10-10)

No change to the integration itself.

Releases are now produced by the release workflow all stefgo projects share. A version can
be tried as a beta before it is released, and every release is described here by hand, above
the list of commits.

## [1.3.5] — 2026-08-29

A maintenance release. The integration and the card behave exactly as in 1.3.4
— what changed is how the project is built, checked and documented, so that all
four ha-custom projects work the same way.

### Added

- `docs/architecture.md`: modules, data flow and the reasoning behind the parts
  that are not obvious — the LAPI's two alert speeds, the local-only ban table,
  the per-query availability. The README's development section points at it.
- ESLint for the card sources. Deliberately syntactic only: the type system is
  `npm --prefix card run typecheck`'s job, and no formatting rules, so it adds
  a check without rewriting working code. It runs in CI next to the type check.

### Changed

- `builddeploy.sh` reads the shared `HA_HOST` / `HA_SSH_PORT` / `HA_CONFIG` /
  `HA_TARGET` names from `.env` instead of the `CROWDSEC_*` ones. **An existing
  local `.env` needs its variable names updated once**; `.env.example` shows the
  new set.
- The release notes come from `.github/scripts/release_notes.py`, which is now
  identical across all four ha-custom repositories. Same behaviour as before:
  no CHANGELOG section for the tag means no release.
- Every CHANGELOG version carries a compare link, and the version headings use
  the same dash as the other projects.

### Fixed

- `_renderFilters()` took a `Counts` argument it never used — found by the new
  lint step, no change in behaviour.
- The rollup build says which of the two bundles it produced (readable or
  minified) instead of leaving it to be guessed.
- The README's links to LICENSE and CHANGELOG pointed at the wrong paths.

## [1.3.4] — 2026-08-23

Maintenance only — the integration and the card behave exactly as in 1.3.3.

### Changed

- The card bundle in the release zip is minified. terser now runs only when
  the build is asked for it with `CROWDSEC_MINIFY=1`, which is set by the
  release workflow and nowhere else: the released bundle is about a third
  smaller, while every local build — `builddeploy.sh` and `watch` included —
  keeps the readable output that lines up with the sources in the browser.
  The minified file carries a `/*! crowdsec-bans-card <version> | MIT */`
  preamble, so the version stays readable in the served file.

## [1.3.3] — 2026-08-23

Maintenance only — the integration and the card behave exactly as in 1.3.2.

### Changed

- The card build can stamp a local build counter into the version:
  `builddeploy.sh` builds with `CROWDSEC_BUILD_COUNTER=1`, so a deployed
  bundle reports `<semver>+build.<n>` and a dashboard still printing the
  previous number is serving a cached bundle. Releases built on GitHub never
  set the flag and stay at the plain semver.
- `builddeploy.sh` takes its target from a git-ignored `.env`
  (see `.env.example`) instead of a hard-coded host.
- Development dependencies: TypeScript 5.9.3 → 6.0.3, vitest 4.1.10 → 4.1.11.

## [1.3.2] — 2026-08-23

Documentation only — the integration and the card are byte-for-byte those of
1.3.1.

### Fixed

- The two card screenshots stayed blank in HACS. They were linked relatively,
  which GitHub resolves against the repository while the Home Assistant
  frontend resolves it against the HA instance — so the browser asked the
  wrong host and got a 404. They are absolute raw URLs now.

### Changed

- The README leads with requirements and installation instead of ~300 lines
  of reference, and has a table of contents; configuration, entities, cards,
  automation, operation and development follow in that order.
- Four statements in it did not match the code and were corrected: the scrape
  duration covers three parallel queries, not two; active decisions always
  comes from `cs_active_decisions` and never from the length of the
  `/v1/decisions` list, which is what makes it count CAPI and blocklist bans;
  reachable means the instance answered on any of the three queries; last
  update is the last scrape in which no query failed.
- The options are a table with defaults and the two validation rules, the
  minimum Home Assistant version is documented rather than living only in
  `hacs.json`, the WebSocket commands are described under Development with
  the note that they are not a stable API, and the YAML examples use `action:`
  instead of the deprecated `service:`.

## [1.3.1] — 2026-08-17

### Fixed

- The card announced itself as version 1.0.0 in the browser console no matter
  which release it shipped in. The number was a second, hand-maintained copy
  that had drifted three releases behind the package it labelled. It is now
  taken from `card/package.json` when the bundle is built — the copy the
  release workflow already checks against the tag — so it cannot go stale
  again. Nothing but the console line changes; the integration itself is
  identical to 1.3.0.

### Internal

- The card is type-checked in CI. The job ran the tests and the bundle but
  never `tsc`, and the elements themselves have no unit tests, so a type error
  in them was only caught when it happened to break the bundler as well.
- TypeScript 7 is held back: it is the native port, and its JavaScript API no
  longer has the shape `@rollup/plugin-typescript` reads. A weekly probe
  builds the card against TypeScript 7 and opens an issue if it ever succeeds,
  so the hold has a way of ending other than somebody remembering it
  (see [rollup/plugins#2016](https://github.com/rollup/plugins/issues/2016)).
- Dependency updates: vitest 4, the rollup plugin group, and the GitHub
  Actions to their current majors, which clears the Node 20 deprecation
  warning from every workflow run.

## [1.3.0] — 2026-08-17

### Changed

- **A sensor now waits on the query its value comes from, not on the cycle as
  a whole.** Each update makes three independent requests, and a single one
  failing used to mark *every* measured value `unavailable` — an alert
  timeout blanked the counters of a metrics scrape that had just succeeded and
  tore a gap into the recorder's statistics for data that was never in doubt.
  With the alert route stuck, `New bans (24 h)` and the three `Top …` sensors
  go unavailable while `Active decisions`, `Lines per minute` and the rest keep
  updating. A failing decision query no longer touches any sensor at all — the
  ban table travels over the WebSocket.
- **`Reachable` means the instance answered at all**, which is the question a
  connectivity sensor is about. It used to mean "all three queries came back"
  and switched off over a single stuck route. That a route is stuck is now
  reported by `Status`, whose `reasons` attribute names it — it lists every
  failed query regardless of reachability, so a permanently broken alert route
  can no longer pass unnoticed behind working metrics.
- `Last update` keeps the strict meaning: the last cycle in which *everything*
  came back, since that is what an automation compares against to spot stale
  values. `Last restart` and `Last alert` are carried over from the previous
  cycle only when their own query failed — a working alert query with an empty
  24 h window has to be able to clear the timestamp instead of freezing it.

### Fixed

- **Diagnostics handed out the banned addresses.** The redaction replaced the
  `value` of every table row but left `key`, which is assembled from the
  address itself (`hist:<ip>:…`, `val:<origin>:<ip>`). Diagnostics get pasted
  into public issues. The key now keeps only its kind; a decision ID, which is
  not an address, stays readable.
- **A ban that worked could be reported as a failure.** After placing the ban
  the command reads the state back for the card, and that read sat outside the
  error handling — a hiccup right after a successful ban surfaced as an error,
  telling the user the opposite of what had happened. The read-back can no
  longer fail the command; a lost answer is flagged the same way the lookup
  flags it, so the card says "cannot tell" instead of guessing.
- The card header printed a literal `{local}`: the text carried a third
  placeholder that the card never filled.
- The instance picker of the bans card showed the wrong entry. The selection
  was set as a property on the `<select>` before its `<option>` elements
  existed, so it fell back to the first one and never corrected itself.
- The lookup card offered an unban button that did nothing for a local
  decision the LAPI does not know by ID. It now falls back to the route by
  address, the same way the ban table already did.

## [1.2.0] — 2026-08-17

### Added

- **Lookup card** (`custom:crowdsec-ip-lookup-card`): checks one address or
  range against every source — local decisions, CAPI and blocklists — and
  finds a range that contains it, which the ban table structurally cannot
  show. Includes the 24 h alert history for the address, plus ban and unban.
  It queries live and covers every origin, because the question is whether the
  address is blocked at all — unlike the ban table, which lists what can be
  acted on.
- Reconfigure flow: addresses and credentials of an existing instance can be
  changed in place instead of deleting the entry and setting it up again.
  Leaving a secret field empty keeps the stored value.
- Repair issue when the LAPI refuses the decision list to the machine token.
  It offers to add a bouncer API key and checks it before storing it — so far
  the reason for an empty ban table sat in a log warning.
- Option `alerts_full_interval` (see below).
- Tests for everything with a Home Assistant dependency (`tests/ha/`), plus
  ruff, mypy, coverage and dependabot in CI.

### Changed

- **Alerts are polled in two speeds.** Every cycle used to refetch the whole
  24h window, which with the window splitting behind it could mean sixteen
  requests per cycle. The window is now kept in the coordinator: a full query
  refreshes it every `alerts_full_interval` seconds (default 300), and each
  cycle only asks for the minutes since the last one. A new ban is still
  noticed within one cycle.
- **The ban card shows local decisions only.** An instance subscribed to a
  blocklist enforces hundreds of thousands of decisions, none of which can be
  lifted from the card, and an address caught by a range would not be findable
  in a table anyway — the lookup card answers that instead. The origin filter
  chips and the card's `origins` option are gone with it, and the "Active
  decisions" sensor keeps counting everything via the metric.
- The card's table is capped at 2000 rows and the rows travel through the
  WebSocket connection page by page instead of in one message.
- The diagnostics no longer contain the LAPI and metrics host names; scheme,
  port and path stay.
- Both cards render the same decision table from one module: same columns,
  same expandable rows, same per-row unban. The lookup card gained the
  country, operator and action columns it lacked.
- The "AS" column is called "Netzblock-Betreiber" / "Network operator" — the
  cell holds the operator of the address block, not the protocol's acronym.
- German wording of the lookup card: it used its own keys and said "Alle
  Decisions entfernen"; it now shares the ban card's phrasing, which names
  the address it acts on.

### Fixed

- Unloading one instance removed the services while a second instance was
  still loaded.
- IP addresses are validated with `ipaddress` instead of a pattern, which let
  `1.2.3.4.5`, `::::` and a `/999` prefix through to the LAPI.
- Ban durations may be composite (`1h30m`); the day unit is now refused up
  front, since neither Go nor CrowdSec knows it.
- The `ip` field of the WebSocket delete command was not validated at all.

## [1.1.0] — 2026-08-15

### Added

- Decision management from Home Assistant: bans can be created and deleted
  through the LAPI, including duration and reason (`decisions.py`).
- WebSocket API as the bridge between the frontend and the decision
  handling (`websocket_api.py`).
- Lovelace card `crowdsec-bans-card` with search, filters, unban button, a
  graphical editor and German/English localisation.
- Release pipeline that runs the tests, builds `crowdsec.zip` for HACS and
  publishes the release.

### Changed

- Reworked API and coordinator layer: pagination, error handling and the
  alert processing were pulled apart into separate modules.
- Code comments and documentation translated from German to English.

## [1.0.1] — 2026-08-14

### Added

- Example package for push notifications and the app badge in the Home
  Assistant Companion app on iOS/iPadOS. It is built for attack peaks and
  sends one buffered summary instead of a push per ban
  (`examples/ios_push_badge.yaml`).

## [1.0.0] — 2026-08-14

### Added

- First release: CrowdSec instance as a device in Home Assistant, with
  sensors for the Prometheus metrics, a problem indicator, services and an
  event on a new ban.

[1.3.5]: https://github.com/stefgo/ha-crowdsec-integration/compare/v1.3.4...v1.3.5
[1.3.4]: https://github.com/stefgo/ha-crowdsec-integration/compare/v1.3.3...v1.3.4
[1.3.3]: https://github.com/stefgo/ha-crowdsec-integration/compare/v1.3.2...v1.3.3
[1.3.2]: https://github.com/stefgo/ha-crowdsec-integration/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/stefgo/ha-crowdsec-integration/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/stefgo/ha-crowdsec-integration/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/stefgo/ha-crowdsec-integration/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/stefgo/ha-crowdsec-integration/compare/1.0.1...v1.1.0
[1.0.1]: https://github.com/stefgo/ha-crowdsec-integration/compare/1.0.0...1.0.1
[1.0.0]: https://github.com/stefgo/ha-crowdsec-integration/releases/tag/1.0.0
