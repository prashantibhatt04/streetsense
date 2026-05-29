import logging
from datetime import datetime, timezone
from specs.data_contracts import UnifiedEvent, SourceFeed, EventType

logger = logging.getLogger(__name__)

SEVERITY_MAP: dict[EventType, int] = {
    EventType.WATERMAIN_BREAK: 3,
    EventType.ROAD_CLOSURE: 3,
    EventType.TRANSIT_DISRUPTION: 2,
    EventType.FLOODING: 4,
    EventType.SEWER_BACKUP: 3,
    EventType.UTILITY_WORK: 1,
    EventType.UNKNOWN: 1,
}


def normalize_severity(event_type: EventType, raw: int) -> int:
    """Blend feed-supplied raw severity with type-based default. Clamps to 0-5."""
    base = SEVERITY_MAP.get(event_type, 1)
    blended = round((base + raw) / 2)
    return max(0, min(5, blended))


def normalize_timestamp(ts: datetime | str | None) -> datetime:
    """Ensure timestamp is timezone-aware UTC. Falls back to now()."""
    if ts is None:
        return datetime.now(timezone.utc)
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def normalize_description(description: str, event_type: EventType) -> str:
    """Ensure description is non-empty; fall back to event type label."""
    desc = (description or "").strip()
    if not desc:
        return event_type.value.replace("_", " ").title()
    return desc


def normalize_event(event: UnifiedEvent) -> UnifiedEvent:
    """
    Apply normalization passes to a UnifiedEvent:
    - severity blending
    - timezone-aware timestamp
    - non-empty description
    Returns a new UnifiedEvent (immutable).
    """
    try:
        return event.model_copy(update={
            "severity_raw": normalize_severity(event.event_type, event.severity_raw),
            "timestamp": normalize_timestamp(event.timestamp),
            "description": normalize_description(event.description, event.event_type),
        })
    except Exception as e:
        logger.warning("Normalization failed for %s: %s", event.event_id, e)
        return event


def normalize_batch(events: list[UnifiedEvent]) -> list[UnifiedEvent]:
    """Normalize a list of events. Never raises — bad events are returned as-is."""
    return [normalize_event(e) for e in events]
