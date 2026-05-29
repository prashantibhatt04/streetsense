import sqlite3
import logging
import json
from pathlib import Path
from specs.data_contracts import UnifiedEvent, WriteResult

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    address TEXT NOT NULL,
    description TEXT NOT NULL,
    severity_raw INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    source_id TEXT NOT NULL,
    metadata TEXT NOT NULL
);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def write_events(events: list[UnifiedEvent], db_path: Path) -> WriteResult:
    if not events:
        return WriteResult(success_count=0, failure_count=0)

    success, failure, errors = 0, 0, []

    try:
        conn = get_connection(db_path)
    except Exception as e:
        return WriteResult(success_count=0, failure_count=len(events), errors=[str(e)])

    with conn:
        for event in events:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events
                    (event_id, source, event_type, latitude, longitude,
                     address, description, severity_raw, timestamp, source_id, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.source.value,
                        event.event_type.value,
                        event.latitude,
                        event.longitude,
                        event.address,
                        event.description,
                        event.severity_raw,
                        event.timestamp.isoformat(),
                        event.source_id,
                        json.dumps(event.metadata),
                    ),
                )
                success += 1
            except Exception as e:
                failure += 1
                errors.append(f"{event.event_id}: {e}")

    conn.close()
    return WriteResult(success_count=success, failure_count=failure, errors=errors)


def read_events(db_path: Path, limit: int = 500) -> list[UnifiedEvent]:
    try:
        conn = get_connection(db_path)
    except Exception as e:
        logger.error("Could not open DB: %s", e)
        return []

    try:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    except Exception as e:
        logger.error("Read failed: %s", e)
        return []
    finally:
        conn.close()

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


def count_events(db_path: Path) -> int:
    try:
        conn = get_connection(db_path)
        result = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        conn.close()
        return result[0]
    except Exception:
        return 0
