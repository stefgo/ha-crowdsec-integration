"""Tests for the per-bouncer evaluation."""

from __future__ import annotations

from datetime import UTC, datetime

from crowdsec_component.bouncers import (
    BouncerTracker,
    decision_requests_by_bouncer,
    route_is_decisions,
)
from crowdsec_component.metrics import MetricSet, Sample, parse_prometheus

START = 1_700_000_000.0
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
METRIC = "cs_lapi_bouncer_requests_total"


def feed(tracker: BouncerTracker, counters, timestamp, start=START):
    return tracker.update(counters, start, timestamp, NOW)


def by_name(tracker: BouncerTracker, ignored=()):
    return {status.name: status for status in tracker.statuses(ignored)}


# -- Reading the metric -----------------------------------------------------


def test_only_the_decision_routes_count():
    metrics = MetricSet(
        parse_prometheus(
            f"""
{METRIC}{{bouncer="traefik",route="/v1/decisions/stream",method="GET"}} 40
{METRIC}{{bouncer="traefik",route="/v1/usage-metrics",method="POST"}} 3
{METRIC}{{bouncer="fw",route="/v1/decisions",method="GET"}} 7
{METRIC}{{route="/v1/decisions",method="GET"}} 99
"""
        )
    )

    assert decision_requests_by_bouncer(metrics, METRIC) == {
        "traefik": 40.0,
        "fw": 7.0,
    }


def test_the_route_label_name_depends_on_the_version():
    for label in ("route", "endpoint", "path"):
        assert route_is_decisions(Sample({label: "/v1/decisions/stream"}, 1.0))
    assert not route_is_decisions(Sample({"route": "/v1/alerts"}, 1.0))
    assert not route_is_decisions(Sample({}, 1.0))


def test_a_missing_metric_gives_no_bouncers():
    assert decision_requests_by_bouncer(MetricSet({}), METRIC) == {}


# -- Evaluation -------------------------------------------------------------


def test_the_first_measurement_cannot_be_evaluated():
    tracker = BouncerTracker(idle_limit=3)
    assert feed(tracker, {"traefik": 10.0}, 0.0) is False
    # Nothing has been seen to move yet, so nothing is known either.
    assert tracker.statuses() == []


def test_a_querying_bouncer_is_active_with_its_rate():
    tracker = BouncerTracker(idle_limit=3)
    feed(tracker, {"traefik": 10.0}, 0.0)

    assert feed(tracker, {"traefik": 16.0}, 60.0) is True

    status = by_name(tracker)["traefik"]
    assert status.active is True
    assert status.queries_per_minute == 6.0
    assert status.last_query == NOW
    assert status.idle_cycles == 0


def test_a_bouncer_appearing_for_the_first_time_counts_from_zero():
    """Prometheus creates the counter on the first request, not before."""
    tracker = BouncerTracker(idle_limit=3)
    feed(tracker, {}, 0.0)

    feed(tracker, {"traefik": 2.0}, 60.0)

    assert by_name(tracker)["traefik"].active is True


def test_it_takes_the_limit_to_call_a_bouncer_stopped():
    tracker = BouncerTracker(idle_limit=3)
    feed(tracker, {"traefik": 10.0}, 0.0)
    feed(tracker, {"traefik": 11.0}, 60.0)

    for cycle in range(1, 3):
        feed(tracker, {"traefik": 11.0}, 60.0 * (cycle + 1))
        # One quiet interval is normal for a bouncer that polls as often as
        # the integration does.
        assert by_name(tracker)["traefik"].active is True

    feed(tracker, {"traefik": 11.0}, 240.0)
    status = by_name(tracker)["traefik"]
    assert status.active is False
    assert status.idle_cycles == 3
    assert status.queries_per_minute == 0.0
    # The last query stays where it was.
    assert status.last_query == NOW


def test_a_query_brings_a_stopped_bouncer_back():
    tracker = BouncerTracker(idle_limit=1)
    feed(tracker, {"traefik": 10.0}, 0.0)
    feed(tracker, {"traefik": 10.0}, 60.0)
    assert tracker.statuses() == []  # never seen querying: not known

    feed(tracker, {"traefik": 11.0}, 120.0)
    feed(tracker, {"traefik": 11.0}, 180.0)
    assert by_name(tracker)["traefik"].active is False

    feed(tracker, {"traefik": 12.0}, 240.0)
    status = by_name(tracker)["traefik"]
    assert status.active is True
    assert status.idle_cycles == 0


def test_one_stopped_bouncer_is_not_hidden_by_another():
    """The case the sum over all bouncers could not tell apart."""
    tracker = BouncerTracker(idle_limit=2)
    feed(tracker, {"traefik": 10.0, "fw": 100.0}, 0.0)
    feed(tracker, {"traefik": 11.0, "fw": 106.0}, 60.0)
    feed(tracker, {"traefik": 11.0, "fw": 112.0}, 120.0)
    feed(tracker, {"traefik": 11.0, "fw": 118.0}, 180.0)

    statuses = by_name(tracker)
    assert statuses["fw"].active is True
    assert statuses["traefik"].active is False


def test_a_restart_of_crowdsec_is_not_evaluated():
    tracker = BouncerTracker(idle_limit=1)
    feed(tracker, {"traefik": 10.0}, 0.0)
    feed(tracker, {"traefik": 11.0}, 60.0)

    # New start time, counters back at zero.
    assert feed(tracker, {}, 120.0, start=START + 500) is False
    status = by_name(tracker)["traefik"]
    assert status.active is True
    assert status.idle_cycles == 0
    assert status.queries_per_minute is None


def test_a_dead_bouncer_missing_after_a_restart_still_goes_off():
    """After a restart its counter does not exist at all — it must not vanish."""
    tracker = BouncerTracker(idle_limit=2)
    feed(tracker, {"traefik": 10.0}, 0.0)
    feed(tracker, {"traefik": 11.0}, 60.0)
    feed(tracker, {}, 120.0, start=START + 500)

    feed(tracker, {}, 180.0, start=START + 500)
    feed(tracker, {}, 240.0, start=START + 500)

    assert by_name(tracker)["traefik"].active is False


def test_a_counter_going_backwards_discards_the_interval():
    tracker = BouncerTracker(idle_limit=1)
    feed(tracker, {"traefik": 10.0}, 0.0)
    feed(tracker, {"traefik": 11.0}, 60.0)

    assert feed(tracker, {"traefik": 5.0}, 120.0) is False
    assert by_name(tracker)["traefik"].active is True


def test_no_time_passed_cannot_be_evaluated():
    tracker = BouncerTracker(idle_limit=1)
    feed(tracker, {"traefik": 10.0}, 60.0)
    assert feed(tracker, {"traefik": 11.0}, 60.0) is False


def test_reset_starts_over_without_losing_the_bouncers():
    tracker = BouncerTracker(idle_limit=1)
    feed(tracker, {"traefik": 10.0}, 0.0)
    feed(tracker, {"traefik": 11.0}, 60.0)

    tracker.reset()

    status = by_name(tracker)["traefik"]
    assert status.active is True
    assert status.queries_per_minute is None
    # The next measurement is a new baseline, not compared to the old one.
    assert feed(tracker, {"traefik": 50.0}, 120.0) is False


def test_the_limit_is_at_least_one():
    assert BouncerTracker(idle_limit=0).idle_limit == 1


# -- Entity registry --------------------------------------------------------


def test_bouncers_from_the_registry_are_known_before_they_query():
    """That is how they survive a restart of Home Assistant."""
    tracker = BouncerTracker(idle_limit=2)
    tracker.sync_registry({"traefik"})

    status = by_name(tracker)["traefik"]
    # Not seen yet, not idle long enough: unknown.
    assert status.active is None

    feed(tracker, {}, 0.0)
    feed(tracker, {}, 60.0)
    assert by_name(tracker)["traefik"].active is None
    feed(tracker, {}, 120.0)
    assert by_name(tracker)["traefik"].active is False


def test_a_deleted_entity_forgets_the_bouncer():
    tracker = BouncerTracker(idle_limit=1)
    tracker.sync_registry({"traefik", "old"})

    tracker.sync_registry({"traefik"})

    assert tracker.names == {"traefik"}


def test_a_new_bouncer_is_kept_until_its_entity_exists():
    """Seen in the metrics but not registered yet is not "deleted"."""
    tracker = BouncerTracker(idle_limit=1)
    feed(tracker, {}, 0.0)
    feed(tracker, {"traefik": 1.0}, 60.0)

    tracker.sync_registry(set())

    assert tracker.names == {"traefik"}


def test_a_deleted_bouncer_stays_away_while_its_counter_stands_still():
    tracker = BouncerTracker(idle_limit=1)
    tracker.sync_registry({"old"})
    feed(tracker, {"old": 5.0}, 0.0)
    tracker.sync_registry(set())

    feed(tracker, {"old": 5.0}, 60.0)
    assert tracker.names == set()

    # ...and comes back once it queries again.
    feed(tracker, {"old": 6.0}, 120.0)
    assert tracker.names == {"old"}


def test_ignored_bouncers_are_left_out():
    tracker = BouncerTracker(idle_limit=1)
    tracker.sync_registry({"traefik", "homeassistant"})

    assert [s.name for s in tracker.statuses({"homeassistant"})] == ["traefik"]
    # Ignored is not forgotten.
    assert tracker.names == {"traefik", "homeassistant"}


def test_the_attribute_shape():
    tracker = BouncerTracker(idle_limit=1)
    feed(tracker, {"traefik": 0.0}, 0.0)
    feed(tracker, {"traefik": 1.0}, 90.0)

    assert tracker.statuses()[0].as_dict() == {
        "name": "traefik",
        "active": True,
        "queries_per_minute": 0.67,
        "last_query": NOW.isoformat(),
    }

    tracker.sync_registry({"traefik", "fw"})
    assert by_name(tracker)["fw"].as_dict() == {
        "name": "fw",
        "active": None,
        "queries_per_minute": None,
        "last_query": None,
    }


def test_an_added_entity_counts_as_registered():
    """Deleted before the next cycle read the registry: still forgotten."""
    tracker = BouncerTracker(idle_limit=1)
    feed(tracker, {}, 0.0)
    feed(tracker, {"traefik": 1.0}, 60.0)

    tracker.mark_registered("traefik")
    tracker.mark_registered("unknown")
    tracker.sync_registry(set())

    assert tracker.names == set()
