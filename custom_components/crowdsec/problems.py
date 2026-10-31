"""Problems of an instance in a form templates and automations can work with.

The status sensor has always listed its reasons as plain text. That is fine to
read, but telling an error from a warning meant matching the wording, which
breaks the next time a sentence changes. Each problem therefore also carries a
severity, a fixed type and the time it first appeared.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .const import SEVERITY_ERROR


@dataclass(frozen=True, slots=True)
class Problem:
    """One reason for the status sensor to be on."""

    severity: str
    type: str
    text: str
    # Only for the problems of a single bouncer.
    bouncer: str | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        """What makes it the *same* problem from one cycle to the next.

        Deliberately not the text: it carries numbers that change every cycle
        (the idle count, the error rate), and the problem does not start over
        because of that.
        """
        return (self.type, self.bouncer)


class ProblemLog:
    """Remembers since when each current problem has existed."""

    def __init__(self) -> None:
        self._since: dict[tuple[str, str | None], datetime] = {}

    def update(
        self, problems: Sequence[Problem], now: datetime
    ) -> list[dict[str, Any]]:
        """The ``problems`` attribute: errors first, then in the given order.

        A problem that went away is forgotten, so it starts with a fresh
        ``since`` when it comes back. After a restart of Home Assistant every
        problem starts at the first cycle.
        """
        current = {problem.key for problem in problems}
        self._since = {key: at for key, at in self._since.items() if key in current}

        result: list[dict[str, Any]] = []
        for problem in problems:
            since = self._since.setdefault(problem.key, now)
            entry: dict[str, Any] = {
                "severity": problem.severity,
                "type": problem.type,
                "text": problem.text,
                "since": since.isoformat(),
            }
            if problem.bouncer is not None:
                entry["bouncer"] = problem.bouncer
            result.append(entry)

        # sorted() is stable, so within one severity the order stays.
        return sorted(result, key=lambda entry: entry["severity"] != SEVERITY_ERROR)
