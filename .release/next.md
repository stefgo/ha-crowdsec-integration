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
