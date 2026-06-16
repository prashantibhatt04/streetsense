import json
import sqlite3
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


# ---------------------------------------------------------------------------
# /health — provider-aware health check
# ---------------------------------------------------------------------------

def test_health_returns_200(client):
    with patch("tools.llm_tools.active_provider_info",
               return_value={"provider": "ollama", "model": "gemma4:latest"}), \
         patch("tools.db_tools.db_event_counts",
               return_value={"total": 5, "db_exists": True}):
        resp = client.get("/health")
    assert resp.status_code == 200


def test_health_returns_provider_and_model(client):
    with patch("tools.llm_tools.active_provider_info",
               return_value={"provider": "ollama", "model": "gemma4:latest"}), \
         patch("tools.db_tools.db_event_counts",
               return_value={"total": 5, "db_exists": True}):
        data = json.loads(client.get("/health").data)
    assert data["status"] == "ok"
    assert data["provider"] == "ollama"
    assert data["model"] == "gemma4:latest"
    assert data["db_events"] == 5
    assert data["db_exists"] is True


def test_health_reflects_claude_provider(client):
    with patch("tools.llm_tools.active_provider_info",
               return_value={"provider": "claude", "model": "claude-haiku-4-5-20251001"}), \
         patch("tools.db_tools.db_event_counts",
               return_value={"total": 0, "db_exists": False}):
        data = json.loads(client.get("/health").data)
    assert data["provider"] == "claude"
    assert data["model"] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# /api/approve and /api/reject — durable DB write
# ---------------------------------------------------------------------------

def _make_cluster_db(tmp_path: Path, cluster_id: str) -> Path:
    """Create a minimal cluster_log DB with a single row (no decision columns yet)."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE cluster_log (
            cluster_id TEXT PRIMARY KEY,
            run_id TEXT,
            cascade_type TEXT,
            severity_score INTEGER,
            brief_headline TEXT,
            brief_body TEXT,
            dispatch_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT INTO cluster_log (cluster_id, run_id, cascade_type, severity_score) VALUES (?,?,?,?)",
        (cluster_id, "run-001", "watermain_to_road_to_ttc", 7),
    )
    conn.commit()
    conn.close()
    return db


def test_approve_writes_human_decision_to_db(client, tmp_path):
    db = _make_cluster_db(tmp_path, "cluster-abc123")
    with patch("dashboard.app.DB_PATH", db):
        resp = client.post("/api/approve/cluster-abc123")
    assert resp.status_code == 200
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT human_decision, decision_at FROM cluster_log WHERE cluster_id=?",
        ("cluster-abc123",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "approved"
    assert row[1] is not None


def test_reject_writes_human_decision_to_db(client, tmp_path):
    db = _make_cluster_db(tmp_path, "cluster-def456")
    with patch("dashboard.app.DB_PATH", db):
        resp = client.post("/api/reject/cluster-def456")
    assert resp.status_code == 200
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT human_decision, decision_at FROM cluster_log WHERE cluster_id=?",
        ("cluster-def456",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "rejected"
    assert row[1] is not None


def test_approve_missing_cluster_id_does_not_raise(client, tmp_path):
    db = _make_cluster_db(tmp_path, "cluster-real")
    with patch("dashboard.app.DB_PATH", db):
        resp = client.post("/api/approve/cluster-nonexistent")
    # UPDATE with no matching row is not an error — 200 with approved status
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "approved"


def test_reject_missing_cluster_id_does_not_raise(client, tmp_path):
    db = _make_cluster_db(tmp_path, "cluster-real")
    with patch("dashboard.app.DB_PATH", db):
        resp = client.post("/api/reject/cluster-nonexistent")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "rejected"


# ---------------------------------------------------------------------------
# /api/comms/<cluster_id> — public communication drafts
# ---------------------------------------------------------------------------

def test_comms_returns_404_when_no_draft(client):
    with patch("dashboard.app._comms_drafts", {}):
        resp = client.get("/api/comms/cluster-nonexistent")
    assert resp.status_code == 404
    data = json.loads(resp.data)
    assert "error" in data


def test_comms_returns_draft_fields_when_present(client):
    from datetime import datetime, timezone
    from specs.data_contracts import PublicCommunicationDraft

    draft = PublicCommunicationDraft(
        cluster_id="cluster-test-comms",
        generated_at=datetime(2024, 10, 2, 13, 0, tzinfo=timezone.utc),
        ttc_alert="511 Bathurst: Service disruption due to watermain break.",
        councillor_email="A watermain break occurred. City departments are responding.",
        social_post="Watermain break on Bathurst causing 511 delays. Use alternate routes.",
        generated_for_severity=7,
    )
    with patch("dashboard.app._comms_drafts", {"cluster-test-comms": draft}):
        resp = client.get("/api/comms/cluster-test-comms")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["cluster_id"] == "cluster-test-comms"
    assert data["ttc_alert"] == draft.ttc_alert
    assert data["councillor_email"] == draft.councillor_email
    assert data["social_post"] == draft.social_post
    assert "generated_at" in data
    assert "char_counts" in data
    assert data["char_counts"]["ttc_alert"] == len(draft.ttc_alert)
    assert data["char_counts"]["social_post"] == len(draft.social_post)
