# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A Home Assistant custom integration (`custom_components/crowdsec/`) that polls one or
more CrowdSec Security Engines, plus a Lit/TypeScript Lovelace card (`card/`) that the
integration serves itself. Each CrowdSec instance is one config entry and one HA device;
the integration can be added multiple times.

## Commands

```bash
# Python tests (no Home Assistant needed — see "Testing" below)
pip install -r requirements_test.txt
python -m pytest
python -m pytest tests/test_decisions.py::test_name    # single test

# The Home-Assistant-dependent tests, in their own environment
pip install -r requirements_test_ha.txt
python -m pytest -c pytest_ha.ini

# Lint and types (ruff over everything, mypy over the HA-free modules)
ruff check . && ruff format --check . && mypy

# Card
npm --prefix card ci
npm --prefix card run build     # writes custom_components/crowdsec/www/ (gitignored)
npm --prefix card run watch     # rebuild on change
npm --prefix card test          # vitest
npm --prefix card run lint      # eslint
npm --prefix card test -- filters.test.ts   # single file

# Build the card and rsync the integration to a live HA instance
./builddeploy.sh                # target from .env, see .env.example
```

`builddeploy.sh` is tracked; the private host lives in `.env` (git-ignored, copy
`.env.example`). It builds with `CROWDSEC_BUILD_COUNTER=1`, so the deployed bundle
reports `<semver>+build.<n>` instead of the bare version: the counter in `card/.build-number`
rises with every local deploy, and a dashboard whose console still prints the old number
served a cached bundle. Releases built on GitHub never set the flag and stay at the
plain semver from `card/package.json`.

The other build flag is `CROWDSEC_MINIFY=1`, set only by the release workflow: terser
runs there and nowhere else, so the bundle in the release zip is minified while every
local build — `builddeploy.sh` and `watch` included — stays readable and lines up with
`src/` when debugging in the browser. The rollup run prints which of the two it did.

## Architecture

The full picture — module table, data flow, the design decisions behind it and
the card bundle — is in [docs/architecture.md](docs/architecture.md). The short
version:

Data flows in one direction: `api.py` → `coordinator.py` → `CrowdSecData` →
entities / websocket / diagnostics.

```
api.py                     the only HTTP layer (Prometheus /metrics + LAPI)
  └── coordinator.py       one CrowdSecData per update cycle
        ├── metrics.py     Prometheus text → MetricSet
        ├── rates.py       counter deltas per minute
        ├── alerts.py      alert JSON → AlertSummary + rolling AlertCache
        ├── decisions.py   decisions + alerts → flat DecisionRecords
        └── timewindow.py  window splitting arithmetic
```

**Keep `metrics.py`, `rates.py`, `alerts.py`, `decisions.py`, `timewindow.py`
and `validation.py` free of Home Assistant imports.** The test suite imports
them without Home Assistant installed, and `mypy` checks exactly those. Anything
that needs `hass` belongs in the coordinator or above — `api.py` sets flags,
`repairs.py` raises the issues.

Three things bite often enough to repeat here; the rest is in the document:

- **Availability is per query, not per cycle** (`metrics_ok`, `alerts_ok`,
  `decisions_ok`, each sensor naming its source via `source_fn`).
- **The ban table is local-only and filtered on this side**, so
  `active_decisions` comes from the `cs_active_decisions` metric, not from the
  list length.
- **`const.INTEGRATION_VERSION` is read from `manifest.json`.** Never add a
  second version constant.

## Testing

Two suites, deliberately apart. `tests/` runs on nothing but pytest — that is what keeps
the pure-logic modules free of Home Assistant — and its coverage floor
(`--cov-fail-under=90`) applies to exactly those modules; the omit list lives in
`pyproject.toml`. `tests/ha/` needs the real framework (`requirements_test_ha.txt`,
`pytest_ha.ini`, its own CI job) and covers the flows, the coordinator, the WebSocket
commands, the services, repairs and diagnostics.

`tests/conftest.py` registers `custom_components/crowdsec` as a synthetic package
(`crowdsec_component`) so relative imports resolve without executing `__init__.py` and
without Home Assistant. Tests must therefore only touch the HA-free modules. `pytest.ini`
sets `--import-mode=importlib` because module names collide otherwise.
`tests/test_integrity.py` keeps `manifest.json`, `const.py`, `strings.json` and the
translations in sync; the card's vitest checks DE and EN carry the same keys and
placeholders.

## Releasing

A release is started by hand, never by pushing a tag: **Actions → Create Release → Run
workflow**, on `main` for a release or on `dev` for a beta (`x.y.z-beta.n`, a prerelease
HACS offers only with beta versions switched on). **Never bump a version or create a
`v*` tag by hand.**

- `dry_run` (on by default) shows the next version and the complete notes in the run
  summary and changes nothing.
- `bump` (`auto` | `patch` | `minor` | `major`): `auto` reads the commit types — `feat`
  raises the minor position, `fix`, `perf` and `revert` the patch position, every other
  type releases nothing — so commit messages follow Conventional Commits. Any other value
  is the step that is taken, whatever the commits say. `major` is the only way a major
  version is created; a `BREAKING CHANGE:` footer raises the minor position.
- **Every release is described by hand in `.release/next.md`** — what is new and what an
  upgrade needs, written for someone who uses the integration. The text goes above the
  generated list of commits, in the GitHub release and in `CHANGELOG.md`; a release
  without it is refused. Write it as part of the change, not at release time. A beta
  keeps the text, the release from `main` empties the file. `.release/footer.md` is the
  installation part appended to every release page.
- **`dev` is merged into `main` with its history — never squashed or rebased** — and
  `main` back into `dev` before the next beta. The workflow checks both.

`.github/workflows/release.yml` calls
[stefgo/release-workflows](https://github.com/stefgo/release-workflows), which carries
semantic-release and its configuration for every stefgo project. It runs `validate.yml`, writes the
version to `manifest.json`, `card/package.json` and `card/package-lock.json`, builds the
minified card and zips `custom_components/crowdsec` (manifest at the archive root, as HACS
expects) *before* the release commit and the tag, and attaches the zip to the release.

`.github/workflows/validate.yml` runs hassfest, HACS validation and pytest on push/PR.
