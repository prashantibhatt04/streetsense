import json
import sys
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.app import app, _serialise_state

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def _make_mock_state(n_events=3):
    from specs.data_contracts import UnifiedEvent, EventType, SourceFeed
    from state.schema import PipelineState
    now = datetime.now(timezone.utc)
    events = [UnifiedEvent(
        event_id=f"evt-{i}",
        source=SourceFeed.ROAD_RESTRICTIONS,
        event_type=EventType.WATERMAIN_BREAK,
        latitude=43.711 + i * 0.001,
        longitude=-79.431,
        address=f"{100+i} Bathurst St",
        description="Test event",
        severity_raw=2,
        timestamp=now,
        source_id=f"src-{i}",
    ) for i in range(n_events)]
    return PipelineState(run_id="test-run-001", started_at=now).with_events(events)

def test_serialise_state_keys():
    result = _serialise_state(_make_mock_state())
    for key in ("mode","run_id","summary","briefs","clusters","events_by_type","events_geo","dispatch","errors"):
        assert key in result


def test_serialise_state_events_geo_has_lat_lng():
    result = _serialise_state(_make_mock_state(n_events=2))
    for e in result["events_geo"]:
        assert "lat" in e and "lng" in e
        assert isinstance(e["lat"], float)

def test_serialise_state_mode_preserved():
    s = _make_mock_state()
    assert _serialise_state(s, mode="live")["mode"] == "live"
    assert _serialise_state(s, mode="replay")["mode"] == "replay"

def test_serialise_state_event_count():
    assert _serialise_state(_make_mock_state(n_events=5))["summary"]["events_ingested"] == 5

def test_serialise_state_empty_briefs():
    result = _serialise_state(_make_mock_state())
    assert result["briefs"] == []
    assert result["summary"]["briefs_generated"] == 0

def test_serialise_state_events_by_type():
    result = _serialise_state(_make_mock_state(n_events=3))
    assert result["events_by_type"].get("watermain_break") == 3

def test_index_returns_200(client):
    assert client.get("/").status_code == 200

def test_index_contains_streetsense(client):
    assert b"StreetSense" in client.get("/").data

def test_index_contains_replay_button(client):
    data = client.get("/").data
    assert b"Replay" in data or b"replay" in data

def test_replay_returns_200(client):
    s = _make_mock_state()
    with patch("dashboard.app.cluster_node", return_value=s), \
         patch("dashboard.app.correlate_node", return_value=s), \
         patch("dashboard.app.impact_node", return_value=s), \
         patch("dashboard.app.brief_node", return_value=s):
        assert client.get("/api/replay").status_code == 200

def test_replay_returns_json(client):
    s = _make_mock_state()
    with patch("dashboard.app.cluster_node", return_value=s), \
         patch("dashboard.app.correlate_node", return_value=s), \
         patch("dashboard.app.impact_node", return_value=s), \
         patch("dashboard.app.brief_node", return_value=s):
        data = json.loads(client.get("/api/replay").data)
    assert data["mode"] == "replay"
    assert "summary" in data

def test_replay_run_id_prefix(client):
    s = _make_mock_state()
    with patch("dashboard.app.cluster_node", return_value=s), \
         patch("dashboard.app.correlate_node", return_value=s), \
         patch("dashboard.app.impact_node", return_value=s), \
         patch("dashboard.app.brief_node", return_value=s):
        data = json.loads(client.get("/api/replay").data)
    assert isinstance(data["run_id"], str) and len(data["run_id"]) > 0

def test_replay_error_returns_500(client):
    with patch("dashboard.app.cluster_node", side_effect=RuntimeError("boom")):
        resp = client.get("/api/replay")
    assert resp.status_code == 500
    assert "error" in json.loads(resp.data)

def test_live_returns_200(client):
    with patch("dashboard.app.run_pipeline", return_value=_make_mock_state()):
        assert client.get("/api/state").status_code == 200

def test_live_returns_json_with_mode(client):
    with patch("dashboard.app.run_pipeline", return_value=_make_mock_state()):
        data = json.loads(client.get("/api/state").data)
    assert data["mode"] == "live"

def test_live_error_returns_500(client):
    with patch("dashboard.app.run_pipeline", side_effect=RuntimeError("feed down")):
        assert client.get("/api/state").status_code == 500

def test_live_summary_keys(client):
    with patch("dashboard.app.run_pipeline", return_value=_make_mock_state(n_events=4)):
        data = json.loads(client.get("/api/state").data)
    for key in ("events_ingested","clusters_found","correlations","impacts","briefs_generated","errors"):
        assert key in data["summary"], f"Missing key: {key}"
