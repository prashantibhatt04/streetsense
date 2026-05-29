import logging
import requests
from datetime import datetime, timezone
from specs.data_contracts import UnifiedEvent, SourceFeed, EventType

logger = logging.getLogger(__name__)

FEED_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/43cbc364-b673-49ca-b98b-8b99c5d5f6eb/resource/3bf43fcc-6c50-441c-862e-afbdb31d9a53/download/utility-cut-permits-data.json"

WORK_TYPE_MAP = {
    "watermain": EventType.WATERMAIN_BREAK,
    "water main": EventType.WATERMAIN_BREAK,
    "sewer": EventType.SEWER_BACKUP,
}


def classify_work_type(work_type: str) -> EventType:
    lowered = work_type.lower()
    for keyword, event_type in WORK_TYPE_MAP.items():
        if keyword in lowered:
            return event_type
    return EventType.UTILITY_WORK


def parse_permit(raw: dict, geocode_fn=None) -> UnifiedEvent | None:
    try:
        address = raw.get("address") or ""
        if not address:
            return None

        lat, lng = None, None
        if geocode_fn:
            coords = geocode_fn(address)
            if coords:
                lat, lng = coords

        if lat is None or lng is None:
            return None

        work_type = raw.get("work_type") or ""

        return UnifiedEvent(
            event_id=str(raw.get("permit_id", "")),
            source=SourceFeed.UTILITY_CUTS,
            event_type=classify_work_type(work_type),
            latitude=lat,
            longitude=lng,
            address=address,
            description=f"{work_type} — {raw.get('client_name', '')}",
            severity_raw=2,
            timestamp=datetime.now(timezone.utc),
            source_id=str(raw.get("permit_id", "")),
            metadata={
                "client_name": raw.get("client_name", ""),
                "status": raw.get("status", ""),
            },
        )
    except Exception as e:
        logger.warning("Skipping utility cut: %s", e)
        return None


def fetch_utility_cuts(geocode_fn=None, limit: int = 200) -> list[UnifiedEvent]:
    try:
        resp = requests.get(FEED_URL, timeout=15)
        resp.raise_for_status()
        records = resp.json()
        records = records[:limit]
        if not isinstance(records, list):
            records = records.get("data", [])
    except Exception as e:
        logger.error("Utility cuts fetch failed: %s", e)
        return []

    results = []
    for record in records:
        event = parse_permit(record, geocode_fn=geocode_fn)
        if event:
            results.append(event)
    return results
