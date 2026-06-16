import json
import pytest
from pathlib import Path
from specs.data_contracts import UnifiedEvent
from tools.geo_tools import cluster_events
from state.schema import PipelineState
from datetime import datetime, timezone

MOCK_DATA = Path(__file__).parent.parent.parent / "evals" / "mock_data"


def load_scenario(filename: str) -> list[UnifiedEvent]:
    path = MOCK_DATA / filename
    data = json.loads(path.read_text())
    events = []
    for raw in data["events"]:
        try:
            events.append(UnifiedEvent(**raw))
        except Exception:
            pass
    return events


# --- oct2024_bathurst ---

def test_bathurst_scenario_loads_three_events():
    events = load_scenario("oct2024_bathurst.json")
    assert len(events) == 3


def test_bathurst_scenario_all_valid_unified_events():
    events = load_scenario("oct2024_bathurst.json")
    for e in events:
        assert isinstance(e, UnifiedEvent)
        assert 43.58 <= e.latitude <= 43.86


def test_bathurst_scenario_forms_one_cluster():
    events = load_scenario("oct2024_bathurst.json")
    clusters = cluster_events(events, radius_metres=300, time_window_minutes=60)
    assert len(clusters) == 1
    assert len(clusters[0].events) == 3


def test_bathurst_scenario_cluster_within_radius():
    events = load_scenario("oct2024_bathurst.json")
    clusters = cluster_events(events, radius_metres=300, time_window_minutes=60)
    assert clusters[0].radius_metres <= 300


def test_bathurst_scenario_time_window():
    events = load_scenario("oct2024_bathurst.json")
    clusters = cluster_events(events, radius_metres=300, time_window_minutes=60)
    assert clusters[0].time_window_minutes <= 60


def test_bathurst_scenario_timestamps_are_morning_edt_in_utc():
    """Event timestamps store Toronto local time correctly offset to UTC —
    08:00-10:00 EDT should appear as 12:00-14:00 UTC."""
    events = load_scenario("oct2024_bathurst.json")
    for e in events:
        assert 12 <= e.timestamp.hour < 14


def test_bathurst_scenario_first_event_is_peak_hours():
    from agents.impact_agent import _is_peak_hours
    events = load_scenario("oct2024_bathurst.json")
    earliest = min(events, key=lambda e: e.timestamp)
    assert _is_peak_hours(earliest.timestamp) is True


# --- single_event adversarial ---

def test_single_event_scenario_no_clusters():
    events = load_scenario("single_event.json")
    clusters = cluster_events(events, radius_metres=300, time_window_minutes=60)
    assert clusters == []


# --- outside_toronto adversarial ---

def test_outside_toronto_scenario_rejected():
    events = load_scenario("outside_toronto.json")
    assert len(events) == 0


# --- pipeline state replay ---

def test_pipeline_state_with_bathurst_events():
    events = load_scenario("oct2024_bathurst.json")
    state = PipelineState(
        run_id="replay-test",
        started_at=datetime.now(timezone.utc),
    )
    state = state.with_events(events)
    assert len(state.raw_events) == 3
    assert state.last_node == "ingest"


def test_pipeline_state_with_clusters():
    events = load_scenario("oct2024_bathurst.json")
    clusters = cluster_events(events)
    state = PipelineState(
        run_id="replay-test",
        started_at=datetime.now(timezone.utc),
    ).with_events(events).with_clusters(clusters)
    assert len(state.clusters) == 1
    assert state.last_node == "cluster"
