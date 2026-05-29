from math import radians, sin, cos, sqrt, atan2
from specs.data_contracts import UnifiedEvent, ClusterCandidate, EventType
import uuid

_FLOOD_CLUSTER_TYPES = {EventType.FLOODING, EventType.SEWER_BACKUP}

EARTH_RADIUS_METRES = 6_371_000


def haversine_metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Return great-circle distance in metres between two lat/lng points.
    Uses the Haversine formula. Accurate to within ~0.5% for city-scale distances.
    """
    r = EARTH_RADIUS_METRES
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def centroid(events: list[UnifiedEvent]) -> tuple[float, float]:
    """Return the mean lat/lng of a list of events."""
    if not events:
        raise ValueError("Cannot compute centroid of empty list")
    lat = sum(e.latitude for e in events) / len(events)
    lng = sum(e.longitude for e in events) / len(events)
    return (lat, lng)


def max_radius_metres(events: list[UnifiedEvent], centre_lat: float, centre_lng: float) -> float:
    """Return the distance in metres from centre to the furthest event."""
    if not events:
        return 0.0
    return max(
        haversine_metres(centre_lat, centre_lng, e.latitude, e.longitude)
        for e in events
    )


def cluster_events(
    events: list[UnifiedEvent],
    radius_metres: float = 300.0,
    time_window_minutes: int = 60,
) -> list[ClusterCandidate]:
    """
    Group events into clusters where every member is within radius_metres
    of the cluster centroid and within time_window_minutes of the earliest event.

    Algorithm: greedy single-pass. Events are processed in timestamp order.
    Each event either joins the first cluster whose centroid is within radius,
    or seeds a new cluster.

    Returns list of ClusterCandidate with 2+ events only.
    Single-event groups are discarded — not enough to be causal.
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e.timestamp)
    clusters: list[list[UnifiedEvent]] = []

    for event in sorted_events:
        placed = False
        for cluster in clusters:
            c_lat, c_lng = centroid(cluster)
            dist = haversine_metres(c_lat, c_lng, event.latitude, event.longitude)
            earliest = min(e.timestamp for e in cluster)
            minutes_diff = (event.timestamp - earliest).total_seconds() / 60

            if dist <= radius_metres and minutes_diff <= time_window_minutes:
                cluster.append(event)
                placed = True
                break

        if not placed:
            clusters.append([event])

    results = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        c_lat, c_lng = centroid(cluster)
        earliest = min(e.timestamp for e in cluster)
        latest = max(e.timestamp for e in cluster)
        time_window = int((latest - earliest).total_seconds() / 60)

        results.append(ClusterCandidate(
            cluster_id=f"cluster-{uuid.uuid4().hex[:8]}",
            events=cluster,
            centroid_lat=c_lat,
            centroid_lng=c_lng,
            radius_metres=max_radius_metres(cluster, c_lat, c_lng),
            time_window_minutes=time_window,
        ))

    return results


def flood_cluster_pass(
    events: list[UnifiedEvent],
    already_clustered_ids: set[str],
    time_window_hours: float = 3.0,
) -> ClusterCandidate | None:
    """
    Second-pass citywide flood clustering: groups flooding/sewer_backup events not
    already in a multi-event cluster, if ≥2 occur within time_window_hours.
    Ignores geographic distance — used when widespread flooding spans the city.
    """
    candidates = [
        e for e in events
        if e.event_type in _FLOOD_CLUSTER_TYPES and e.event_id not in already_clustered_ids
    ]
    if len(candidates) < 2:
        return None

    sorted_cands = sorted(candidates, key=lambda e: e.timestamp)
    earliest = sorted_cands[0].timestamp
    latest = sorted_cands[-1].timestamp
    hours_span = (latest - earliest).total_seconds() / 3600

    if hours_span > time_window_hours:
        return None

    c_lat, c_lng = centroid(sorted_cands)
    time_window = int((latest - earliest).total_seconds() / 60)

    return ClusterCandidate(
        cluster_id=f"cluster-flood-{uuid.uuid4().hex[:8]}",
        events=sorted_cands,
        centroid_lat=c_lat,
        centroid_lng=c_lng,
        radius_metres=max_radius_metres(sorted_cands, c_lat, c_lng),
        time_window_minutes=time_window,
    )
