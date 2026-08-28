"""Assemble the body of a GitHub release from CHANGELOG.md.

Usage: release_notes.py <version> [<output file>]

Writes to the output file, or to stdout when none is given.

The same script sits in every ha-custom repository; only the configuration
block below differs. It reads the section of the version being released and
refuses to produce anything when that section is missing or empty — writing
the entry is part of releasing, and a release page that only lists commits
says nothing about what changed.

The changelog carries what changed; the installation part is identical for
every release and is appended from FOOTER, so it cannot drift between entries.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- configuration -------------------------------------------------------
REPO = "stefgo/ha-crowdsec-integration"

FOOTER = f"""
## Installation

**HACS → ⋮ → Custom repositories** → `{REPO}`, category **Integration** →
*Add*. Then install "CrowdSec" and restart Home Assistant.

Manually: copy `custom_components/crowdsec/` from the attached zip to
`<config>/custom_components/` and restart Home Assistant.

Full documentation is in the [README](https://github.com/{REPO}#readme).
"""
# -------------------------------------------------------------------------

# "## [1.2.3] — 2026-08-23" — the date is optional, the brackets are not.
HEADING = re.compile(r"^## \[([^\]]+)\][^\n]*$", re.MULTILINE)


def sections(changelog: str) -> list[tuple[str, str]]:
    """Every ``## [version]`` section as (version, body), in file order."""
    headings = list(HEADING.finditer(changelog))
    result = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
        body = changelog[heading.end() : end]
        # The link definitions at the end of the file belong to no section.
        body = re.split(r"^\[[^\]]+\]: ", body, maxsplit=1, flags=re.MULTILINE)[0]
        result.append((heading.group(1).strip(), body.strip()))
    return result


def main() -> None:
    if not 2 <= len(sys.argv) <= 3:
        raise SystemExit(__doc__)
    version = sys.argv[1].lstrip("v")
    found = sections(Path("CHANGELOG.md").read_text(encoding="utf-8"))
    versions = [entry[0] for entry in found]
    if version not in versions:
        raise SystemExit(
            f"CHANGELOG.md has no section '## [{version}]' — add one before tagging"
        )
    index = versions.index(version)
    body = found[index][1]
    if not body:
        raise SystemExit(f"The section '## [{version}]' in CHANGELOG.md is empty")

    previous = versions[index + 1] if index + 1 < len(versions) else ""
    compare = (
        f"https://github.com/{REPO}/compare/v{previous}...v{version}"
        if previous
        else f"https://github.com/{REPO}/commits/v{version}"
    )
    notes = f"{body}\n{FOOTER}\n**Full Changelog**: {compare}\n"

    if len(sys.argv) == 3:
        Path(sys.argv[2]).write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)


if __name__ == "__main__":
    main()
