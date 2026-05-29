import re
import json
import logging
import requests
from specs.data_contracts import UnifiedEvent, SourceFeed, EventType
from datetime import datetime, timezone
from config import TORONTO_BBOX

logger = logging.getLogger(__name__)

FEED_URL = "https://secure.toronto.ca/opendata/cart/road_restrictions/v3?format=json"

WORK_TYPE_MAP = {
    "watermain": EventType.WATERMAIN_BREAK,
    "water main": EventType.WATERMAIN_BREAK,
    "road closure": EventType.ROAD_CLOSURE,
    "emergency": EventType.ROAD_CLOSURE,
}


def fix_backslash_escapes(raw: str) -> str:
    """Remove invalid backslash escapes that break json.loads."""
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)


def classify_work_type(work_type: str) -> EventType:
    lowered = work_type.lower()
    for keyword, event_type in WORK_TYPE_MAP.items():
        if keyword in lowered:
            return event_type
    return EventType.UNKNOWN


def is_within_toronto(lat: float, lng: float) -> bool:
    return (
        TORONTO_BBOX["lat_min"] <= lat <= TORONTO_BBOX["lat_max"]
        and TORONTO_BBOX["lng_min"] <= lng <= TORONTO_BBOX["lng_max"]
    )


def parse_restriction(raw: dict) -> UnifiedEvent | None:
    try:
        lat = float(raw["latitude"])
        lng = float(raw["longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    if not is_within_toronto(lat, lng):
        return None

    work_type = raw.get("work_type") or ""
    event_type = classify_work_type(work_type)

    try:
        return UnifiedEvent(
            event_id=str(raw.get("id", "")),
            source=SourceFeed.ROAD_RESTRICTIONS,
            event_type=event_type,
            latitude=lat,
            longitude=lng,
            address=raw.get("location") or "",
            description=work_type,
            severity_raw=3 if "emergency" in work_type.lower() else 2,
            timestamp=datetime.now(timezone.utc),
            source_id=str(raw.get("id", "")),
            metadata={"contractor": raw.get("contractor", "")},
        )
    except Exception as e:
        logger.warning("Skipping restriction: %s", e)
        return None


def fetch_road_restrictions() -> list[UnifiedEvent]:
    try:
        resp = requests.get(FEED_URL, timeout=10)
        resp.raise_for_status()
        fixed = fix_backslash_escapes(resp.text)
        data = json.loads(fixed)
        records = data if isinstance(data, list) else data.get("features", [])
    except Exception as e:
        logger.error("Road restrictions fetch failed: %s", e)
        return []

    results = []
    for record in records:
        event = parse_restriction(record)
        if event:
            results.append(event)
    return results
