"""
IngestionAgent — single responsibility: poll feeds, validate, geocode, write to store.
Tools: fetch_road_restrictions, fetch_ttc_alerts, fetch_utility_cuts,
       fetch_311_requests, geocode_address, write_events.
Does NOT cluster, reason, or generate briefs.
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from ingestion.feeds.road_restrictions import fetch_road_restrictions
from ingestion.feeds.ttc_alerts import fetch_ttc_alerts
from ingestion.feeds.utility_cuts import fetch_utility_cuts
from ingestion.feeds.requests_311 import fetch_311_requests
from ingestion.geocoder import geocode_address
from ingestion.normalizer import normalize_batch
from ingestion.store import write_events
from specs.data_contracts import UnifiedEvent, WriteResult
from state import agent_log
from config import CLUSTER_RADIUS_M

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "streetsense.db"


class IngestionAgent:
    """
    Polls all four Toronto open data feeds concurrently.
    Validates every record through Pydantic before writing to SQLite.
    Resilient: a single feed failure does not abort the others.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    async def run_cycle(self) -> list[UnifiedEvent]:
        """
        Single ingestion cycle. Fetches all feeds concurrently.
        Returns list of successfully validated + normalized new events.
        """
        agent_log.append("Ingestion: fetching all 4 feeds concurrently…")

        raw_results = await asyncio.gather(
            asyncio.to_thread(fetch_road_restrictions),
            asyncio.to_thread(fetch_ttc_alerts),
            asyncio.to_thread(lambda: fetch_utility_cuts(geocode_fn=geocode_address, limit=200)),
            asyncio.to_thread(lambda: fetch_311_requests(geocode_fn=geocode_address, limit=200)),
            return_exceptions=True,
        )

        feed_names = ["road_restrictions", "ttc_alerts", "utility_cuts", "requests_311"]
        all_events: list[UnifiedEvent] = []

        for name, result in zip(feed_names, raw_results):
            if isinstance(result, Exception):
                logger.warning("Feed %s failed: %s", name, result)
                agent_log.append(f"⚠ Feed {name} error: {result}")
            else:
                all_events.extend(result)
                agent_log.append(f"  {name}: {len(result)} events")

        normalized = normalize_batch(all_events)
        if normalized:
            write_result = write_events(normalized, self.db_path)
            agent_log.append(
                f"Ingestion complete: {write_result.success_count} written, "
                f"{write_result.failure_count} skipped"
            )
        else:
            agent_log.append("Ingestion: no valid events this cycle")

        return normalized

    def run_cycle_sync(self) -> list[UnifiedEvent]:
        """Synchronous wrapper for use in non-async contexts."""
        return asyncio.run(self.run_cycle())
