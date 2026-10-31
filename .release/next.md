<!--
The hand-written part of the next release: what is new, why it matters, and
what an upgrade needs. It is placed above the list of commits in the GitHub
release and in CHANGELOG.md.

Write below this comment. Use "###" for headings -- "##" is the level of the
version itself. A release with nothing written here is refused. A beta from
dev keeps the text; the stable release from main empties this file again.

Nothing inside an HTML comment is published.
-->

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

- **Ready for Home Assistant 2027.8.** The integration no longer uses a device
  registry call that Home Assistant removes in 2027.8 and warns about until then.
