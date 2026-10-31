"""Tests for the structured problem list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crowdsec_component.problems import Problem, ProblemLog

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(minutes=5)


def test_errors_come_first_and_the_order_stays_otherwise():
    log = ProblemLog()
    result = log.update(
        [
            Problem("warning", "parse-errors", "a"),
            Problem("error", "no-log-lines", "b"),
            Problem("warning", "query-failed", "c"),
            Problem("error", "bouncer-idle", "d", bouncer="traefik"),
        ],
        T0,
    )

    assert [entry["text"] for entry in result] == ["b", "d", "a", "c"]


def test_the_entry_shape():
    log = ProblemLog()
    plain, bouncer = log.update(
        [
            Problem("warning", "parse-errors", "rate high"),
            Problem("error", "bouncer-idle", "stopped", bouncer="traefik"),
        ],
        T0,
    )[::-1]

    assert plain == {
        "severity": "warning",
        "type": "parse-errors",
        "text": "rate high",
        "since": T0.isoformat(),
    }
    assert bouncer["bouncer"] == "traefik"


def test_since_stays_while_the_problem_lasts_even_if_the_text_changes():
    log = ProblemLog()
    log.update([Problem("error", "bouncer-idle", "for 5", bouncer="a")], T0)

    (entry,) = log.update(
        [Problem("error", "bouncer-idle", "for 6", bouncer="a")], LATER
    )

    assert entry["since"] == T0.isoformat()
    assert entry["text"] == "for 6"


def test_a_problem_that_went_away_starts_over():
    log = ProblemLog()
    log.update([Problem("warning", "parse-errors", "x")], T0)
    log.update([], T0 + timedelta(minutes=1))

    (entry,) = log.update([Problem("warning", "parse-errors", "x")], LATER)

    assert entry["since"] == LATER.isoformat()


def test_each_bouncer_has_its_own_since():
    log = ProblemLog()
    log.update([Problem("error", "bouncer-idle", "a", bouncer="a")], T0)

    entries = log.update(
        [
            Problem("error", "bouncer-idle", "a", bouncer="a"),
            Problem("error", "bouncer-idle", "b", bouncer="b"),
        ],
        LATER,
    )

    assert {e["bouncer"]: e["since"] for e in entries} == {
        "a": T0.isoformat(),
        "b": LATER.isoformat(),
    }
