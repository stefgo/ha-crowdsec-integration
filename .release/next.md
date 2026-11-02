<!--
The hand-written part of the next release: what is new, why it matters, and
what an upgrade needs. It is placed above the list of commits in the GitHub
release and in CHANGELOG.md.

Write below this comment. Use "###" for headings -- "##" is the level of the
version itself. A release with nothing written here is refused. A beta from
dev keeps the text; the stable release from main empties this file again.

Nothing inside an HTML comment is published.
-->

### Fixed

- **No more "API key not found" errors in the CrowdSec log.** The integration
  asked for the decision list with its machine credentials first, which
  CrowdSec always refuses, and logged in again on every poll before falling
  back to the bouncer key. It now asks with the bouncer key straight away; the
  log stays clean and the needless login is gone.

### Upgrade notes

- **The ban table and the IP lookup need a bouncer API key.** CrowdSec hands
  the decision list to bouncers only — that was always the case, the
  integration just tried the machine credentials first. Without a key nothing
  is asked any more and Home Assistant offers a repair to add one
  (`cscli bouncers add homeassistant`). The `Active decisions` sensor works
  without a key as before.
