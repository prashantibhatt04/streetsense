import io
import logging
import zipfile
import requests
import csv
from datetime import datetime, timezone
from specs.data_contracts import UnifiedEvent, SourceFeed, EventType

logger = logging.getLogger(__name__)

FEED_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/108c2bd1-6945-46f6-af92-02f5658ee7f7/resource/99b7f283-7345-4f5a-a126-d078ed4f3419/download/311-service-requests-2026.csv"

WATER_EVENT_TYPES = {
    "Watermain-Possible Break": EventType.WATERMAIN_BREAK,
    "Storm Event-Flooding": EventType.FLOODING,
    "Maintenance Hole - Overflowing": EventType.SEWER_BACKUP,
    "Sewer main-Backup": EventType.SEWER_BACKUP,
    "Catch Basin - Blocked / Flooding": EventType.FLOODING,
    "Road Water Ponding": EventType.FLOODING,
}


def classify_request_type(service_request_type: str) -> EventType | None:
    return WATER_EVENT_TYPES.get(service_request_type, None)


def build_address(street1: str, street2: str) -> str | None:
    s1 = (street1 or "").strip()
    s2 = (street2 or "").strip()
    if not s1:
        return None
    if s2:
        return f"{s1} & {s2}, Toronto, ON"
    return f"{s1}, Toronto, ON"


def parse_row(row: dict, geocode_fn=None) -> UnifiedEvent | None:
    try:
        event_type = classify_request_type(row.get("Service Request Type", ""))
        if event_type is None:
            return None

        address = build_address(
            row.get("Intersection Street 1", ""),
            row.get("Intersection Street 2", ""),
        )
        if not address:
            return None

        lat, lng = None, None
        if geocode_fn:
            coords = geocode_fn(address)
            if coords:
                lat, lng = coords

        if lat is None or lng is None:
            return None

        raw_date = row.get("Creation Date", "")
        try:
            timestamp = datetime.fromisoformat(raw_date).replace(tzinfo=timezone.utc)
        except ValueError:
            timestamp = datetime.now(timezone.utc)

        source_id = row.get("Service Request #") or row.get("id", "")

        return UnifiedEvent(
            event_id=f"311-{source_id}",
            source=SourceFeed.REQUESTS_311,
            event_type=event_type,
            latitude=lat,
            longitude=lng,
            address=address,
            description=row.get("Service Request Type", ""),
            severity_raw=2,
            timestamp=timestamp,
            source_id=str(source_id),
            metadata={
                "ward": row.get("Ward", ""),
                "status": row.get("Status", ""),
            },
        )
    except Exception as e:
        logger.warning("Skipping 311 row: %s", e)
        return None


def parse_csv_bytes(data: bytes) -> list[dict]:
    text = data.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def fetch_311_requests(geocode_fn=None,limit: int = 200) -> list[UnifiedEvent]:
    try:
        resp = requests.get(FEED_URL, timeout=30)
        resp.raise_for_status()
        content = resp.content

        if content[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
                data = zf.read(csv_name)
        else:
            data = content

        rows = parse_csv_bytes(data)
        rows = rows[:limit] 
    except Exception as e:
        logger.error("311 fetch failed: %s", e)
        return []

    results = []
    for row in rows:
        event = parse_row(row, geocode_fn=geocode_fn)
        if event:
            results.append(event)
    return results
