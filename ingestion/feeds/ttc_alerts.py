import logging
import requests
from datetime import datetime, timezone
from specs.data_contracts import UnifiedEvent, SourceFeed, EventType

logger = logging.getLogger(__name__)

FEED_URL = "https://gtfsrt.ttc.ca/alerts/all?format=text"

ROUTE_STOPS = {
    "511": (43.7120, -79.4310),
    "512": (43.6544, -79.4040),
    "504": (43.6497, -79.3717),
    "505": (43.6545, -79.4195),
    "506": (43.6628, -79.3800),
}

DEFAULT_TTC_COORD = (43.7000, -79.4000)


def parse_severity(severity: str) -> int:
    return {"INFO": 1, "WARNING": 2, "SEVERE": 4, "UNKNOWN_SEVERITY": 1}.get(severity.upper(), 1)


def route_to_coords(route_id: str) -> tuple[float, float]:
    return ROUTE_STOPS.get(route_id, DEFAULT_TTC_COORD)


def parse_gtfsrt_text(text: str) -> list[dict]:
    """
    Parse GTFS-RT text format into list of alert dicts.
    Each alert block is separated by blank lines.
    Fields are key: value pairs.
    """
    alerts = []
    current: dict = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current:
                alerts.append(current)
                current = {}
            continue
        if ": " in line:
            key, _, value = line.partition(": ")
            current[key.strip().lower().replace(" ", "_")] = value.strip()

    if current:
        alerts.append(current)

    return alerts


def parse_alert(raw: dict) -> UnifiedEvent | None:
    try:
        route_id = raw.get("route_id", "")
        lat, lng = route_to_coords(route_id)
        severity = raw.get("severity", "INFO")

        return UnifiedEvent(
            event_id=raw.get("alert_id") or raw.get("id") or f"ttc-{route_id}-{datetime.now(timezone.utc).timestamp()}",
            source=SourceFeed.TTC_ALERTS,
            event_type=EventType.TRANSIT_DISRUPTION,
            latitude=lat,
            longitude=lng,
            address=f"TTC Route {route_id}",
            description=raw.get("description") or raw.get("header") or "",
            severity_raw=parse_severity(severity),
            timestamp=datetime.now(timezone.utc),
            source_id=raw.get("alert_id") or "",
            metadata={"route_id": route_id, "stop_id": raw.get("stop_id", "")},
        )
    except Exception as e:
        logger.warning("Skipping TTC alert: %s", e)
        return None


def fetch_ttc_alerts() -> list[UnifiedEvent]:
    try:
        resp = requests.get(FEED_URL, timeout=10, headers={"User-Agent": "StreetSense/1.0 (toronto-hackathon)"})
        resp.raise_for_status()
        records = parse_gtfsrt_text(resp.text)
    except Exception as e:
        logger.error("TTC fetch failed: %s", e)
        return []

    results = []
    for record in records:
        event = parse_alert(record)
        if event:
            results.append(event)
    return results
