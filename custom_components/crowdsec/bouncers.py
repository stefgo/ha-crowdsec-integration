"""Whether each bouncer still fetches decisions.

CrowdSec itself blocks nothing — a bouncer pulls the decisions from the LAPI
and enforces them. If it stops, detection carries on as before and nothing is
blocked any more, which is invisible from the outside. Counting the requests
of all bouncers together hides the one that stopped as long as another one is
still running, so the count is kept per bouncer.

The source is ``cs_lapi_bouncer_requests_total``: it counts only requests made
with a bouncer key, so the integration's own queries with the machine token
are not in it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .metrics import MetricSet, Sample
from .rates import START_TIME_TOLERANCE

# Label names under which CrowdSec stores the LAPI route, depending on version.
ROUTE_LABELS = ("endpoint", "route", "path")
BOUNCER_LABEL = "bouncer"
DECISIONS_ROUTE = "/v1/decisions"


def route_is_decisions(sample: Sample) -> bool:
    """Does the sample hit one of the decision routes of the LAPI?

    ``/v1/decisions`` and ``/v1/decisions/stream`` — the routes a bouncer uses
    to learn what to block. Other bouncer routes such as ``/v1/usage-metrics``
    say nothing about whether decisions are being enforced.
    """
    for label in ROUTE_LABELS:
        value = sample.labels.get(label)
        if value and value.startswith(DECISIONS_ROUTE):
            return True
    return False


def decision_requests_by_bouncer(metrics: MetricSet, name: str) -> dict[str, float]:
    """Requests to the decision routes per bouncer name."""
    grouped: dict[str, float] = {}
    for sample in metrics.samples(name):
        bouncer = sample.labels.get(BOUNCER_LABEL)
        if not bouncer or not route_is_decisions(sample):
            continue
        grouped[bouncer] = grouped.get(bouncer, 0.0) + sample.value
    return grouped


@dataclass(frozen=True, slots=True)
class BouncerStatus:
    """What is known about one bouncer after a cycle."""

    name: str
    # ``None`` until it is known: no query seen yet, but not idle long enough
    # to call it stopped either — right after a start, for instance.
    active: bool | None
    queries_per_minute: float | None
    last_query: datetime | None
    idle_cycles: int

    def as_dict(self) -> dict[str, Any]:
        """The shape of one entry in the ``bouncers`` attribute."""
        return {
            "name": self.name,
            "active": self.active,
            "queries_per_minute": (
                None
                if self.queries_per_minute is None
                else round(self.queries_per_minute, 2)
            ),
            "last_query": (
                None if self.last_query is None else self.last_query.isoformat()
            ),
        }


@dataclass(slots=True)
class _Entry:
    # Seen in the entity registry at least once. A registered bouncer that is
    # gone from the registry was deleted by the user and is forgotten.
    registered: bool = False
    active: bool | None = None
    rate: float | None = None
    last_query: datetime | None = None
    idle_cycles: int = 0


class BouncerTracker:
    """Keeps the request counters per bouncer across cycles.

    A bouncer stays known once it is: after a restart of CrowdSec the counter
    of a bouncer that does not query any more is missing from the metrics
    altogether — Prometheus only creates it on the first request. Treating
    "missing" as "gone" would let exactly the broken bouncer disappear.
    """

    def __init__(self, idle_limit: int) -> None:
        self._idle_limit = max(1, idle_limit)
        self._entries: dict[str, _Entry] = {}
        self._counters: dict[str, float] | None = None
        self._start_time: float | None = None
        self._timestamp: float | None = None

    @property
    def idle_limit(self) -> int:
        """Cycles without a query until a bouncer counts as stopped."""
        return self._idle_limit

    @property
    def names(self) -> set[str]:
        """All bouncers currently known."""
        return set(self._entries)

    def sync_registry(self, names: Iterable[str]) -> None:
        """Align the known bouncers with the entity registry.

        Names in the registry are known — that is how the bouncers survive a
        restart of Home Assistant. A bouncer whose entity was deleted is
        forgotten; it only comes back if it queries again.
        """
        present = set(names)
        for name in [n for n, e in self._entries.items() if e.registered]:
            if name not in present:
                del self._entries[name]
        for name in present:
            self._entries.setdefault(name, _Entry()).registered = True

    def mark_registered(self, name: str) -> None:
        """The bouncer's entity exists now.

        Without this, an entity deleted before the next cycle read the
        registry would look like a new bouncer still waiting for its entity —
        and be created again straight away.
        """
        entry = self._entries.get(name)
        if entry is not None:
            entry.registered = True

    def reset(self) -> None:
        """Discard the last measurement, e.g. after a failed scrape."""
        self._counters = None
        self._start_time = None
        self._timestamp = None
        for entry in self._entries.values():
            entry.rate = None

    def update(
        self,
        counters: Mapping[str, float],
        start_time: float | None,
        timestamp: float,
        now: datetime,
    ) -> bool:
        """Record a measurement and evaluate it against the previous one.

        Returns whether the cycle could be evaluated. It cannot on the first
        measurement, after a restart of CrowdSec and when a counter went
        backwards — then the idle counts stay where they are rather than
        moving on a guess.
        """
        previous = self._counters
        previous_start = self._start_time
        previous_timestamp = self._timestamp

        self._counters = dict(counters)
        self._start_time = start_time
        self._timestamp = timestamp

        window = self._deltas(
            previous, previous_start, previous_timestamp, start_time, timestamp
        )
        if window is None:
            for known in self._entries.values():
                known.rate = None
            return False

        deltas, elapsed = window
        for name, delta in deltas.items():
            entry = self._entries.get(name)
            if entry is None:
                # A counter that did not move is no reason to start watching —
                # that is the case of a deleted bouncer whose counter lingers
                # until the next restart of CrowdSec.
                if delta <= 0:
                    continue
                entry = self._entries[name] = _Entry()
            entry.rate = delta / elapsed * 60.0
            if delta > 0:
                entry.idle_cycles = 0
                entry.active = True
                entry.last_query = now
            else:
                entry.idle_cycles += 1
                if entry.idle_cycles >= self._idle_limit:
                    entry.active = False
        return True

    def _deltas(
        self,
        previous: dict[str, float] | None,
        previous_start: float | None,
        previous_timestamp: float | None,
        start_time: float | None,
        timestamp: float,
    ) -> tuple[dict[str, float], float] | None:
        """Increase per bouncer and elapsed seconds, or ``None``."""
        if previous is None or previous_timestamp is None:
            return None
        elapsed = timestamp - previous_timestamp
        if elapsed <= 0:
            return None
        if (
            start_time is not None
            and previous_start is not None
            and abs(start_time - previous_start) > START_TIME_TOLERANCE
        ):
            return None

        current = self._counters or {}
        deltas: dict[str, float] = {}
        # Within one run of CrowdSec a missing counter means "no request yet",
        # so it counts as 0 on either side.
        for name in set(current) | set(previous) | set(self._entries):
            value = current.get(name, 0.0)
            before = previous.get(name, 0.0)
            if value < before:
                return None
            deltas[name] = value - before
        return deltas, elapsed

    def statuses(self, ignored: Iterable[str] = ()) -> list[BouncerStatus]:
        """The known bouncers without the ignored ones, sorted by name."""
        skip = set(ignored)
        return [
            BouncerStatus(
                name=name,
                active=entry.active,
                queries_per_minute=entry.rate,
                last_query=entry.last_query,
                idle_cycles=entry.idle_cycles,
            )
            for name, entry in sorted(self._entries.items())
            if name not in skip
        ]
