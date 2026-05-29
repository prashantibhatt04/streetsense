"""
Adversarial boundary tests for StreetSense.
These verify that the system refuses or handles gracefully on bad/edge inputs.
Run: python3 -m pytest evals/test_adversarial.py -v
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

MOCK_DATA = Path(__file__).parent / "mock_data"


# ---------------------------------------------------------------------------
# 1. Events outside Toronto are rejected at the feed layer
# ---------------------------------------------------------------------------

def test_rejects_coordinates_outside_toronto_south():
    """Mississauga coordinates (lat 43.10) must be rejected by parse_restriction."""
    from ingestion.feeds.road_restrictions import parse_restriction
    raw = {
        "id": "test-missisauga",
        "latitude": 43.10,
        "longitude": -79.60,
        "work_type": "Watermain",
        "location": "300 City Centre Dr, Mississauga",
    }
    result = parse_restriction(raw)
    assert result is None, "Event outside Toronto south boundary must be rejected"


def test_rejects_coordinates_outside_toronto_west():
    """Coordinates too far west (lng -80.0) must be rejected."""
    from ingestion.feeds.road_restrictions import parse_restriction
    raw = {
        "id": "test-west",
        "latitude": 43.70,
        "longitude": -80.00,
        "work_type": "Road Closure",
        "location": "Somewhere west of Toronto",
    }
    result = parse_restriction(raw)
    assert result is None, "Event outside Toronto west boundary must be rejected"


def test_outside_toronto_scenario_produces_zero_clusters():
    """The outside_toronto.json scenario must produce zero clusters end-to-end."""
    from specs.data_contracts import UnifiedEvent
    from tools.geo_tools import cluster_events

    path = MOCK_DATA / "outside_toronto.json"
    data = json.loads(path.read_text())
    events = []
    for raw in data["events"]:
        try:
            events.append(UnifiedEvent(**raw))
        except Exception:
            pass  # Pydantic rejection counts as correct behaviour

    # Either the model rejected all events, or clustering finds nothing
    clusters = cluster_events(events, radius_metres=300, time_window_minutes=60)
    assert len(events) == 0 or len(clusters) == 0, (
        "Events outside Toronto must not form clusters"
    )


# ---------------------------------------------------------------------------
# 2. Malformed JSON in a feed must not crash — returns empty list
# ---------------------------------------------------------------------------

def test_malformed_json_feed_returns_empty_list_not_crash():
    """Road restrictions feed with invalid JSON escape must return [] not raise."""
    from ingestion.feeds.road_restrictions import fetch_road_restrictions
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    # Deliberately broken JSON that would crash standard json.loads
    mock_resp.text = '{"features": [{"id": "bad\\escape\\path", "latitude": 43.71, "longitude": -79.43}]}'
    mock_resp.raise_for_status = MagicMock()

    with patch("ingestion.feeds.road_restrictions.requests.get", return_value=mock_resp):
        result = fetch_road_restrictions()

    assert isinstance(result, list), "Feed must always return a list, never raise"


def test_network_failure_returns_empty_list_not_crash():
    """Any network failure on any feed must return [] not raise."""
    from ingestion.feeds.road_restrictions import fetch_road_restrictions
    from ingestion.feeds.ttc_alerts import fetch_ttc_alerts
    from ingestion.feeds.utility_cuts import fetch_utility_cuts
    from ingestion.feeds.requests_311 import fetch_311_requests

    with patch("ingestion.feeds.road_restrictions.requests.get", side_effect=ConnectionError("down")):
        assert fetch_road_restrictions() == []

    with patch("ingestion.feeds.ttc_alerts.requests.get", side_effect=ConnectionError("down")):
        assert fetch_ttc_alerts() == []

    with patch("ingestion.feeds.utility_cuts.requests.get", side_effect=ConnectionError("down")):
        assert fetch_utility_cuts() == []

    with patch("ingestion.feeds.requests_311.requests.get", side_effect=ConnectionError("down")):
        assert fetch_311_requests() == []


# ---------------------------------------------------------------------------
# 3. Single-event cluster must never reach LLM — returns is_causal=False
# ---------------------------------------------------------------------------

def test_single_event_cluster_never_calls_llm():
    """correlate_cluster must short-circuit on single-event clusters without LLM."""
    from agents.correlation_agent import correlate_cluster
    from specs.data_contracts import UnifiedEvent, SourceFeed, EventType, ClusterCandidate
    from datetime import datetime, timezone

    single = UnifiedEvent(
        event_id="solo-001",
        source=SourceFeed.REQUESTS_311,
        event_type=EventType.WATERMAIN_BREAK,
        latitude=43.7115,
        longitude=-79.4317,
        address="Bathurst St & Prue Ave, Toronto, ON",
        description="Watermain-Possible Break",
        severity_raw=2,
        timestamp=datetime(2024, 10, 2, 8, 43, 0, tzinfo=timezone.utc),
        source_id="solo-001",
        metadata={},
    )
    cluster = ClusterCandidate(
        cluster_id="cluster-solo",
        events=[single],
        centroid_lat=single.latitude,
        centroid_lng=single.longitude,
        radius_metres=0.0,
        time_window_minutes=0,
    )

    with patch("agents.correlation_agent.call_llm_json") as mock_llm:
        result = correlate_cluster(cluster)
        mock_llm.assert_not_called()

    assert result.is_causal is False
    assert result.confidence == 0.0


def test_single_event_scenario_produces_no_clusters():
    """The single_event.json scenario must produce zero clusters."""
    from specs.data_contracts import UnifiedEvent
    from tools.geo_tools import cluster_events

    path = MOCK_DATA / "single_event.json"
    data = json.loads(path.read_text())
    events = [UnifiedEvent(**e) for e in data["events"]]
    clusters = cluster_events(events, radius_metres=300, time_window_minutes=60)
    assert clusters == [], "Single event must not form a cluster"


# ---------------------------------------------------------------------------
# 4. Circuit breaker stops pipeline at iteration ceiling
# ---------------------------------------------------------------------------

def test_circuit_breaker_stops_ingest_node():
    """ingest_node must return an error state when iteration_count >= 10."""
    from state.graph import ingest_node
    from state.schema import PipelineState
    from datetime import datetime, timezone

    state = PipelineState(
        run_id="circuit-test",
        started_at=datetime.now(timezone.utc),
    ).model_copy(update={"iteration_count": 10})

    result = ingest_node(state, [])
    assert any("Circuit breaker" in e for e in result.errors), (
        "ingest_node must log circuit breaker error when iteration ceiling is hit"
    )


def test_circuit_breaker_stops_cluster_node():
    """cluster_node must return an error state when iteration_count >= 10."""
    from state.graph import cluster_node
    from state.schema import PipelineState
    from datetime import datetime, timezone

    state = PipelineState(
        run_id="circuit-test",
        started_at=datetime.now(timezone.utc),
    ).model_copy(update={"iteration_count": 10})

    result = cluster_node(state)
    assert any("Circuit breaker" in e for e in result.errors)


def test_circuit_breaker_stops_correlate_node():
    """correlate_node must return an error state when iteration_count >= 10."""
    from state.graph import correlate_node
    from state.schema import PipelineState
    from datetime import datetime, timezone

    state = PipelineState(
        run_id="circuit-test",
        started_at=datetime.now(timezone.utc),
    ).model_copy(update={"iteration_count": 10})

    result = correlate_node(state)
    assert any("Circuit breaker" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 5. LLM failure must never crash the pipeline — always returns a result
# ---------------------------------------------------------------------------

def test_correlation_llm_total_failure_returns_fallback():
    """correlate_cluster must return a non-causal CorrelationResult if LLM always fails."""
    from agents.correlation_agent import correlate_cluster
    from specs.data_contracts import CorrelationResult

    # Use bathurst_cluster-like data inline (no fixture dependency in evals)
    from specs.data_contracts import UnifiedEvent, SourceFeed, EventType, ClusterCandidate
    from datetime import datetime, timezone

    events = [
        UnifiedEvent(
            event_id=f"ev-{i}",
            source=SourceFeed.REQUESTS_311,
            event_type=EventType.WATERMAIN_BREAK,
            latitude=43.711 + i * 0.0001,
            longitude=-79.431,
            address="Bathurst St, Toronto, ON",
            description="Watermain break",
            severity_raw=2,
            timestamp=datetime(2024, 10, 2, 8, 43 + i, 0, tzinfo=timezone.utc),
            source_id=f"ev-{i}",
            metadata={},
        )
        for i in range(3)
    ]
    cluster = ClusterCandidate(
        cluster_id="cluster-fail-test",
        events=events,
        centroid_lat=43.7111,
        centroid_lng=-79.431,
        radius_metres=50.0,
        time_window_minutes=5,
    )

    with patch("agents.correlation_agent.call_llm_json", side_effect=Exception("LLM down")):
        result = correlate_cluster(cluster)

    assert isinstance(result, CorrelationResult)
    assert result.is_causal is False


def test_malformed_feed_fixture_parse_resilience():
    """
    malformed_feed.json contains bad escapes, null ids, wrong types.
    parse_restriction must return None for each bad record and not raise.
    The one valid record must still be parsed correctly.
    """
    from ingestion.feeds.road_restrictions import parse_restriction

    path = MOCK_DATA / "malformed_feed.json"
    data = json.loads(path.read_text())
    results = [parse_restriction(r) for r in data["records"]]

    # Bad records → None; exactly one valid record → UnifiedEvent
    non_none = [r for r in results if r is not None]
    assert len(non_none) == 1, f"Expected 1 valid record, got {len(non_none)}"
    assert non_none[0].event_id != "", "Valid record must have a non-empty event_id"


def test_briefing_llm_total_failure_returns_fallback():
    """generate_brief must return a fallback OperationalBrief if LLM always fails."""
    from agents.briefing_agent import generate_brief
    from specs.data_contracts import (
        OperationalBrief, ClusterCandidate, CorrelationResult,
        ImpactAssessment, UnifiedEvent, SourceFeed, EventType,
    )
    from datetime import datetime, timezone
    from config import MODEL

    event = UnifiedEvent(
        event_id="ev-brief-1",
        source=SourceFeed.REQUESTS_311,
        event_type=EventType.WATERMAIN_BREAK,
        latitude=43.7115,
        longitude=-79.4317,
        address="Bathurst St, Toronto, ON",
        description="Watermain break",
        severity_raw=2,
        timestamp=datetime(2024, 10, 2, 9, 0, 0, tzinfo=timezone.utc),
        source_id="ev-brief-1",
        metadata={},
    )
    event2 = event.model_copy(update={"event_id": "ev-brief-2", "latitude": 43.7120})
    cluster = ClusterCandidate(
        cluster_id="cluster-brief-fail",
        events=[event, event2],
        centroid_lat=43.7117,
        centroid_lng=-79.4317,
        radius_metres=60.0,
        time_window_minutes=10,
    )
    correlation = CorrelationResult(
        cluster_id=cluster.cluster_id,
        is_causal=True,
        confidence=0.85,
        cascade_type="watermain_to_road",
        causal_chain=["Watermain break", "Road closure"],
        reasoning="Clear causal chain.",
        llm_model=MODEL,
    )
    impact = ImpactAssessment(
        cluster_id=cluster.cluster_id,
        severity_score=7,
        affected_routes=["511"],
        estimated_duration_hours=2.0,
        recommended_actions=["Dispatch crew"],
    )

    with patch("agents.briefing_agent.call_llm_json", side_effect=Exception("LLM down")):
        result = generate_brief(cluster, correlation, impact)

    assert isinstance(result, OperationalBrief)
    assert len(result.headline) > 0
