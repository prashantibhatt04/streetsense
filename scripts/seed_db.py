"""
One-time (or periodic) data seed script.

Downloads all 4 Toronto open-data feeds, validates every record through
the Pydantic models, and writes to the local SQLite database.

Usage:
    python3 -m scripts.seed_db                   # seeds all feeds
    python3 -m scripts.seed_db --feeds rr ttc    # specific feeds only
    python3 -m scripts.seed_db --limit 500       # cap records per feed
    python3 -m scripts.seed_db --geocoder demo   # use offline geocoder (fast)

After running this once, the pipeline reads from the DB — no internet needed.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.store import write_events, count_events, get_connection
from ingestion.geocoder import geocode_address
from specs.data_contracts import WriteResult
from tools.db_tools import seed_pattern_memory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("seed_db")

DB_PATH = ROOT / "streetsense.db"


def _init_tables():
    """Ensure all tables exist including cluster_log and pattern_memory."""
    conn = get_connection(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cluster_log (
            cluster_id   TEXT PRIMARY KEY,
            run_id       TEXT,
            cascade_type TEXT,
            severity_score INTEGER,
            brief_headline TEXT,
            brief_body   TEXT,
            dispatch_json TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pattern_memory (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            cascade_type        TEXT,
            corridor            TEXT,
            similar_date        TEXT,
            outcome             TEXT,
            uncoordinated_hours REAL,
            confidence          REAL DEFAULT 0.5,
            observed_date       TEXT,
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.close()


# ---------------------------------------------------------------------------
# Per-feed seeders
# ---------------------------------------------------------------------------

def seed_road_restrictions(limit: int) -> WriteResult:
    logger.info("Seeding road restrictions…")
    from ingestion.feeds.road_restrictions import fetch_road_restrictions
    events = fetch_road_restrictions()
    events = events[:limit]
    logger.info("  fetched %d events", len(events))
    result = write_events(events, DB_PATH)
    logger.info("  written %d  skipped %d  errors %d",
                result.success_count, result.failure_count, len(result.errors))
    if result.errors:
        for e in result.errors[:3]:
            logger.warning("    %s", e)
    return result


def seed_ttc_alerts(limit: int) -> WriteResult:
    logger.info("Seeding TTC alerts…")
    from ingestion.feeds.ttc_alerts import fetch_ttc_alerts
    events = fetch_ttc_alerts()
    events = events[:limit]
    logger.info("  fetched %d events", len(events))
    result = write_events(events, DB_PATH)
    logger.info("  written %d  skipped %d  errors %d",
                result.success_count, result.failure_count, len(result.errors))
    return result


def seed_utility_cuts(limit: int, geocoder_mode: str) -> WriteResult:
    logger.info("Seeding utility cuts (geocoding: %s)…", geocoder_mode)
    from ingestion.feeds.utility_cuts import fetch_utility_cuts

    if geocoder_mode == "demo":
        os.environ["STREETSENSE_GEOCODER"] = "demo"
        geo_fn = geocode_address
    else:
        geo_fn = geocode_address

    events = fetch_utility_cuts(geocode_fn=geo_fn, limit=limit)
    logger.info("  geocoded+fetched %d events", len(events))
    result = write_events(events, DB_PATH)
    logger.info("  written %d  skipped %d  errors %d",
                result.success_count, result.failure_count, len(result.errors))
    return result


def seed_311_requests(limit: int, geocoder_mode: str) -> WriteResult:
    logger.info("Seeding 311 requests (geocoding: %s)…", geocoder_mode)
    logger.info("  (311 CSV is ~50MB — first download takes ~30s on good wifi)")
    from ingestion.feeds.requests_311 import fetch_311_requests

    if geocoder_mode == "demo":
        os.environ["STREETSENSE_GEOCODER"] = "demo"
        geo_fn = geocode_address
    else:
        geo_fn = geocode_address

    events = fetch_311_requests(geocode_fn=geo_fn, limit=limit)
    logger.info("  geocoded+fetched %d events", len(events))
    result = write_events(events, DB_PATH)
    logger.info("  written %d  skipped %d  errors %d",
                result.success_count, result.failure_count, len(result.errors))
    return result


def seed_scenario(name: str) -> WriteResult:
    """Load a named scenario JSON file from evals/mock_data/ into the DB."""
    import json
    from specs.data_contracts import UnifiedEvent

    path = ROOT / "evals" / "mock_data" / f"{name}.json"
    if not path.exists():
        logger.warning("Scenario file not found: %s", path)
        return WriteResult(success_count=0, failure_count=0)

    logger.info("Seeding scenario %s…", name)
    data = json.loads(path.read_text())
    events = []
    for raw in data["events"]:
        try:
            events.append(UnifiedEvent(**raw))
        except Exception as e:
            logger.warning("  skipping event: %s", e)

    result = write_events(events, DB_PATH)
    logger.info("  written %d events from %s", result.success_count, name)
    return result


def seed_bathurst_scenario() -> WriteResult:
    return seed_scenario("oct2024_bathurst")


def seed_queen_st_scenario() -> WriteResult:
    return seed_scenario("queen_st_active")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

FEED_MAP = {
    "rr":       ("Road Restrictions", seed_road_restrictions),
    "ttc":      ("TTC Alerts", seed_ttc_alerts),
    "utility":  ("Utility Cuts", seed_utility_cuts),
    "311":      ("311 Requests", seed_311_requests),
    "bathurst": ("Bathurst Scenario (Oct 2024)", seed_bathurst_scenario),
    "queen":    ("Queen St W Scenario", seed_queen_st_scenario),
}


def main():
    parser = argparse.ArgumentParser(description="Seed StreetSense local DB")
    parser.add_argument(
        "--feeds", nargs="+",
        choices=list(FEED_MAP.keys()) + ["all"],
        default=["bathurst", "queen"],
        help="Which feeds to seed (default: all)",
    )
    parser.add_argument(
        "--limit", type=int, default=300,
        help="Max records per feed (default: 300)",
    )
    parser.add_argument(
        "--geocoder", choices=["nominatim", "demo"], default="demo",
        help="Geocoder to use for feeds without lat/lng (default: demo — fast, offline)",
    )
    args = parser.parse_args()

    feeds = list(FEED_MAP.keys()) if "all" in args.feeds else args.feeds

    logger.info("=" * 60)
    logger.info("StreetSense DB seed — %s", DB_PATH)
    logger.info("Feeds: %s  |  limit: %d  |  geocoder: %s",
                ", ".join(feeds), args.limit, args.geocoder)
    logger.info("=" * 60)

    _init_tables()
    before = count_events(DB_PATH)
    logger.info("Events in DB before seed: %d", before)

    # Always seed pattern_memory with known historical patterns
    n = seed_pattern_memory()
    if n:
        logger.info("Pattern memory: seeded %d historical cascade patterns", n)

    t0 = time.time()
    total_written = 0

    for feed_key in feeds:
        name, fn = FEED_MAP[feed_key]
        logger.info("")
        try:
            # Functions that need geocoder get extra args
            if feed_key in ("utility", "311"):
                result = fn(args.limit, args.geocoder)
            elif feed_key in ("rr", "ttc"):
                result = fn(args.limit)
            else:
                result = fn()
            total_written += result.success_count
        except Exception as e:
            logger.error("  %s failed: %s", name, e)

    after = count_events(DB_PATH)
    elapsed = time.time() - t0

    logger.info("")
    logger.info("=" * 60)
    logger.info("Seed complete in %.1fs", elapsed)
    logger.info("Events before: %d  |  after: %d  |  new: %d",
                before, after, after - before)
    logger.info("DB path: %s  (%.1f KB)", DB_PATH,
                DB_PATH.stat().st_size / 1024 if DB_PATH.exists() else 0)
    logger.info("=" * 60)
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Start dashboard:  python3 dashboard/app.py")
    logger.info("  2. Start daemon:     python3 main.py --mode daemon")
    logger.info("  3. Or just replay:   python3 -m evals.replay oct2024_bathurst")


if __name__ == "__main__":
    main()
