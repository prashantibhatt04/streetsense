"""
ACI tools for reading from the local SQLite event store.
These are the DB-backed equivalents of the live feed fetch functions.
After seeding with scripts/seed_db.py, all pipeline operations use these
instead of hitting external APIs — enabling fully air-gapped operation.
"""

import logging
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from specs.data_contracts import UnifiedEvent, SourceFeed

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "streetsense.db"


def _read_events_from_db(
    db_path: Path,
    source: SourceFeed | None = None,
    hours: float = 72,
    limit: int = 500,
) -> list[UnifiedEvent]:
    """
    Read events from SQLite, optionally filtered by source and recency.
    Returns [] if DB doesn't exist or any error occurs — never raises.
    """
    if not db_path.exists():
        logger.warning("DB not found at %s — run scripts/seed_db.py first", db_path)
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        if source:
            rows = conn.execute(
                """SELECT * FROM events
                   WHERE source = ? AND timestamp >= ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (source.value, cutoff, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM events
                   WHERE timestamp >= ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (cutoff, limit),
            ).fetchall()

        conn.close()
    except Exception as e:
        logger.error("DB read failed: %s", e)
        return []

    events = []
    for row in rows:
        try:
            events.append(UnifiedEvent(
                event_id=row["event_id"],
                source=row["source"],
                event_type=row["event_type"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                address=row["address"],
                description=row["description"],
                severity_raw=row["severity_raw"],
                timestamp=row["timestamp"],
                source_id=row["source_id"],
                metadata=json.loads(row["metadata"]),
            ))
        except Exception as e:
            logger.warning("Skipping malformed row: %s", e)

    return events


def fetch_road_restrictions_from_db(hours: float = 72) -> list[UnifiedEvent]:
    """
    Read road restriction events from local DB.
    Use instead of fetch_road_restrictions() when running air-gapped.
    hours: how far back to look (default 72 = 3 days of events)
    """
    events = _read_events_from_db(DB_PATH, SourceFeed.ROAD_RESTRICTIONS, hours)
    logger.info("DB: %d road restriction events (last %.0fh)", len(events), hours)
    return events


def fetch_ttc_alerts_from_db(hours: float = 24) -> list[UnifiedEvent]:
    """
    Read TTC alert events from local DB.
    TTC alerts are more transient so default window is shorter.
    """
    events = _read_events_from_db(DB_PATH, SourceFeed.TTC_ALERTS, hours)
    logger.info("DB: %d TTC alert events (last %.0fh)", len(events), hours)
    return events


def fetch_utility_cuts_from_db(hours: float = 720) -> list[UnifiedEvent]:
    """
    Read utility cut events from local DB.
    Permits are long-lived so default window is 30 days.
    """
    events = _read_events_from_db(DB_PATH, SourceFeed.UTILITY_CUTS, hours)
    logger.info("DB: %d utility cut events (last %.0fh)", len(events), hours)
    return events


def fetch_311_requests_from_db(hours: float = 48) -> list[UnifiedEvent]:
    """Read 311 service request events from local DB."""
    events = _read_events_from_db(DB_PATH, SourceFeed.REQUESTS_311, hours)
    logger.info("DB: %d 311 events (last %.0fh)", len(events), hours)
    return events


def fetch_all_from_db(hours: float = 72) -> list[UnifiedEvent]:
    """
    Read all recent events from DB regardless of source.
    Primary feed function for the DB-backed pipeline mode.
    """
    events = _read_events_from_db(DB_PATH, source=None, hours=hours)
    logger.info("DB: %d total events across all sources (last %.0fh)", len(events), hours)
    return events


def db_event_counts() -> dict:
    """Return per-source event counts from the DB. Used by dashboard status endpoint."""
    if not DB_PATH.exists():
        return {"total": 0, "db_exists": False}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM events GROUP BY source"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()
        counts = {r[0]: r[1] for r in rows}
        counts["total"] = total
        counts["db_exists"] = True
        counts["db_size_kb"] = round(DB_PATH.stat().st_size / 1024, 1)
        return counts
    except Exception as e:
        logger.error("db_event_counts failed: %s", e)
        return {"total": 0, "db_exists": True, "error": str(e)}


def lookup_historical_pattern(cascade_type: str, corridor: str) -> "HistoricalMatch":
    """
    Look up pattern_memory for a matching past cascade on the same corridor.
    Returns HistoricalMatch(match_found=False) if nothing found — never raises.
    corridor: a street keyword like 'bathurst', 'queen', 'king'
    """
    from specs.data_contracts import HistoricalMatch

    if not DB_PATH.exists():
        return HistoricalMatch(match_found=False)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            """SELECT similar_date, outcome, uncoordinated_hours, confidence, corridor
               FROM pattern_memory
               WHERE cascade_type = ? AND corridor LIKE ?
               ORDER BY confidence DESC, id DESC
               LIMIT 1""",
            (cascade_type, f"%{corridor.lower()}%"),
        ).fetchone()
        conn.close()

        if row:
            return HistoricalMatch(
                match_found=True,
                similar_date=row[0],
                corridor=row[4],
                outcome=row[1],
                uncoordinated_hours=row[2],
                confidence=row[3] or 0.7,
            )
        return HistoricalMatch(match_found=False)
    except Exception as e:
        logger.warning("lookup_historical_pattern failed: %s", e)
        return HistoricalMatch(match_found=False)


def seed_pattern_memory() -> int:
    """
    Insert known historical cascade patterns.
    Called once by scripts/seed_db.py.
    Returns number of rows inserted.
    """
    if not DB_PATH.exists():
        logger.warning("DB not found — cannot seed pattern_memory")
        return 0

    patterns = [
        # Oct 2024 Bathurst cascade — the real event that went uncoordinated 4 hours
        ("watermain_to_road_to_ttc", "bathurst",
         "2024-10-02",
         "Watermain break at Bathurst & Prue triggered road closure and 511 streetcar "
         "detour. Three departments had separate tickets. Coordination took 4 hours.",
         4.0, 0.92),
        # Queen St cascade — watermain work disrupted 501 streetcar
        ("watermain_to_road_to_ttc", "queen",
         "2024-08-14",
         "Watermain replacement on Queen St W caused 501 streetcar short-turn at "
         "Roncesvalles for 6 hours. TTC notified 2 hours after road closure.",
         6.0, 0.81),
        # King St utility work
        ("utility_to_road", "king",
         "2024-06-22",
         "Utility excavation on King St E required road closure. 504 King streetcar "
         "diverted for 3 hours before coordination.",
         3.0, 0.74),
        # Flooding cascade — heavy rain event
        ("flooding_cascade", "dundas",
         "2024-07-16",
         "Heavy rain caused road flooding on Dundas St W. 505 Dundas bus rerouted. "
         "Catch basin crews dispatched 90 minutes after first 311 report.",
         1.5, 0.68),
    ]

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pattern_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cascade_type TEXT,
                corridor TEXT,
                similar_date TEXT,
                outcome TEXT,
                uncoordinated_hours REAL,
                confidence REAL DEFAULT 0.5,
                observed_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        inserted = 0
        for cascade_type, corridor, date, outcome, hours, conf in patterns:
            existing = conn.execute(
                "SELECT id FROM pattern_memory WHERE cascade_type=? AND corridor=? AND similar_date=?",
                (cascade_type, corridor, date)
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO pattern_memory
                       (cascade_type, corridor, similar_date, outcome, uncoordinated_hours, confidence)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (cascade_type, corridor, date, outcome, hours, conf)
                )
                inserted += 1
        conn.commit()
        conn.close()
        logger.info("Seeded %d historical patterns into pattern_memory", inserted)
        return inserted
    except Exception as e:
        logger.error("seed_pattern_memory failed: %s", e)
        return 0


def write_cluster_result(cluster_id: str, run_id: str, cascade_type: str,
                         severity_score: int, brief_headline: str,
                         brief_body: str) -> None:
    """
    Persist a pipeline result to cluster_log for the memory agent and dashboard history.
    Silent on failure — never interrupts the pipeline.
    """
    if not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT OR REPLACE INTO cluster_log
            (cluster_id, run_id, cascade_type, severity_score, brief_headline, brief_body)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (cluster_id, run_id, cascade_type, severity_score, brief_headline, brief_body))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("cluster_log write failed: %s", e)
