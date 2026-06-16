import sqlite3
import pytest
from pathlib import Path
from tools.db_tools import ensure_cluster_log_decision_columns


def _make_cluster_log(conn: sqlite3.Connection) -> None:
    """Create a cluster_log table without the new decision columns (pre-migration state)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_log (
            cluster_id   TEXT PRIMARY KEY,
            run_id       TEXT,
            cascade_type TEXT,
            severity_score INTEGER,
            brief_headline TEXT,
            brief_body   TEXT,
            dispatch_json TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(cluster_log)")}


# ---------------------------------------------------------------------------
# ensure_cluster_log_decision_columns
# ---------------------------------------------------------------------------

def test_adds_human_decision_column(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    _make_cluster_log(conn)
    assert "human_decision" not in _columns(conn)
    ensure_cluster_log_decision_columns(conn)
    assert "human_decision" in _columns(conn)
    conn.close()


def test_adds_decision_at_column(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    _make_cluster_log(conn)
    assert "decision_at" not in _columns(conn)
    ensure_cluster_log_decision_columns(conn)
    assert "decision_at" in _columns(conn)
    conn.close()


def test_idempotent_when_columns_already_present(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    _make_cluster_log(conn)
    ensure_cluster_log_decision_columns(conn)
    # Second call must not raise
    ensure_cluster_log_decision_columns(conn)
    cols = _columns(conn)
    assert "human_decision" in cols
    assert "decision_at" in cols
    conn.close()


def test_preserves_existing_columns(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    _make_cluster_log(conn)
    ensure_cluster_log_decision_columns(conn)
    cols = _columns(conn)
    for expected in ("cluster_id", "run_id", "cascade_type", "severity_score",
                     "brief_headline", "brief_body", "created_at"):
        assert expected in cols
    conn.close()
