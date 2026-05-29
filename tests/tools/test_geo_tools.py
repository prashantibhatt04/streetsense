import pytest
from datetime import datetime, timezone, timedelta
from tools.geo_tools import haversine_metres, centroid, max_radius_metres, cluster_events, flood_cluster_pass
from specs.data_contracts import UnifiedEvent, SourceFeed, EventType


# --- haversine_metres ---

def test_haversine_same_point():
    assert haversine_metres(43.7115, -79.4317, 43.7115, -79.4317) == 0.0

def test_haversine_known_distance():
    # Bathurst & Bloor to Bathurst & Dupont — roughly 400m apart
    dist = haversine_metres(43.6662, -79.4114, 43.6742, -79.4114)
    assert 400 < dist < 1000

def test_haversine_returns_float():
    result = haversine_metres(43.71, -79.43, 43.72, -79.44)
    assert isinstance(result, float)


# --- centroid ---

def test_centroid_single_event(bathurst_watermain_event):
    lat, lng = centroid([bathurst_watermain_event])
    assert lat == bathurst_watermain_event.latitude
    assert lng == bathurst_watermain_event.longitude

def test_centroid_three_events(bathurst_cluster):
    lat, lng = centroid(bathurst_cluster.events)
    assert 43.58 <= lat <= 43.86
    assert -79.64 <= lng <= -79.11

def test_centroid_empty_raises():
    with pytest.raises(ValueError):
        centroid([])


# --- max_radius_metres ---

def test_max_radius_single_event(bathurst_watermain_event):
    r = max_radius_metres(
        [bathurst_watermain_event],
        bathurst_watermain_event.latitude,
        bathurst_watermain_event.longitude,
    )
    assert r == 0.0

def test_max_radius_empty():
    assert max_radius_metres([], 43.71, -79.43) == 0.0

def test_max_radius_cluster(bathurst_cluster):
    c_lat, c_lng = centroid(bathurst_cluster.events)
    r = max_radius_metres(bathurst_cluster.events, c_lat, c_lng)
    assert r >= 0.0


# --- cluster_events ---

def make_event(event_id, lat, lng, minutes_offset=0):
    return UnifiedEvent(
        event_id=event_id,
        source=SourceFeed.REQUESTS_311,
        event_type=EventType.WATERMAIN_BREAK,
        latitude=lat,
        longitude=lng,
        address="Test address",
        description="Test",
        severity_raw=2,
        timestamp=datetime(2024, 10, 2, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes_offset),
        source_id=event_id,
    )


def test_cluster_empty_input():
    assert cluster_events([]) == []

def test_cluster_single_event_discarded():
    event = make_event("e1", 43.7115, -79.4317)
    result = cluster_events([event])
    assert result == []

def test_cluster_two_nearby_events():
    e1 = make_event("e1", 43.7115, -79.4317, minutes_offset=0)
    e2 = make_event("e2", 43.7116, -79.4318, minutes_offset=10)
    result = cluster_events([e1, e2], radius_metres=300)
    assert len(result) == 1
    assert len(result[0].events) == 2

def test_cluster_far_apart_events_not_clustered():
    e1 = make_event("e1", 43.7115, -79.4317)
    e2 = make_event("e2", 43.7800, -79.3500)  # several km away
    result = cluster_events([e1, e2], radius_metres=300)
    assert result == []  # both are singles, discarded

def test_cluster_outside_time_window_not_clustered():
    e1 = make_event("e1", 43.7115, -79.4317, minutes_offset=0)
    e2 = make_event("e2", 43.7116, -79.4318, minutes_offset=120)
    result = cluster_events([e1, e2], radius_metres=300, time_window_minutes=60)
    assert result == []

def test_cluster_bathurst_scenario(bathurst_cluster):
    result = cluster_events(bathurst_cluster.events, radius_metres=300)
    assert len(result) == 1
    assert len(result[0].events) == 3


# --- flood_cluster_pass ---

def make_flood_event(event_id, lat, lng, minutes_offset=0, event_type=EventType.FLOODING):
    return UnifiedEvent(
        event_id=event_id,
        source=SourceFeed.REQUESTS_311,
        event_type=event_type,
        latitude=lat,
        longitude=lng,
        address="Test address",
        description="Test",
        severity_raw=2,
        timestamp=datetime(2024, 7, 16, 12, 45, tzinfo=timezone.utc) + timedelta(minutes=minutes_offset),
        source_id=event_id,
    )


def test_flood_cluster_pass_groups_citywide_events():
    # Two flood events far apart but within the time window
    e1 = make_flood_event("f1", 43.66, -79.36, minutes_offset=0)
    e2 = make_flood_event("f2", 43.64, -79.42, minutes_offset=90)  # ~5km away
    result = flood_cluster_pass([e1, e2], already_clustered_ids=set())
    assert result is not None
    assert len(result.events) == 2
    assert result.cluster_id.startswith("cluster-flood-")


def test_flood_cluster_pass_single_event_returns_none():
    e1 = make_flood_event("f1", 43.66, -79.36)
    assert flood_cluster_pass([e1], already_clustered_ids=set()) is None


def test_flood_cluster_pass_excludes_already_clustered():
    e1 = make_flood_event("f1", 43.66, -79.36, minutes_offset=0)
    e2 = make_flood_event("f2", 43.64, -79.42, minutes_offset=60)
    result = flood_cluster_pass([e1, e2], already_clustered_ids={"f1", "f2"})
    assert result is None


def test_flood_cluster_pass_outside_time_window_returns_none():
    e1 = make_flood_event("f1", 43.66, -79.36, minutes_offset=0)
    e2 = make_flood_event("f2", 43.64, -79.42, minutes_offset=240)  # 4 hours
    result = flood_cluster_pass([e1, e2], already_clustered_ids=set(), time_window_hours=3.0)
    assert result is None


def test_flood_cluster_pass_includes_sewer_backup():
    e1 = make_flood_event("f1", 43.66, -79.36, event_type=EventType.FLOODING)
    e2 = make_flood_event("f2", 43.64, -79.42, minutes_offset=60, event_type=EventType.SEWER_BACKUP)
    result = flood_cluster_pass([e1, e2], already_clustered_ids=set())
    assert result is not None
    assert len(result.events) == 2


def test_flood_cluster_pass_excludes_watermain_events():
    e1 = make_flood_event("f1", 43.66, -79.36, event_type=EventType.WATERMAIN_BREAK)
    e2 = make_flood_event("f2", 43.64, -79.42, minutes_offset=60, event_type=EventType.WATERMAIN_BREAK)
    assert flood_cluster_pass([e1, e2], already_clustered_ids=set()) is None


def test_flood_cluster_pass_ignores_already_local_clustered_events():
    # One flooding event already in a local cluster, one is not — only 1 candidate → None
    e1 = make_flood_event("f1", 43.66, -79.36, minutes_offset=0)
    e2 = make_flood_event("f2", 43.64, -79.42, minutes_offset=60)
    result = flood_cluster_pass([e1, e2], already_clustered_ids={"f1"})
    assert result is None  # only f2 is a candidate — not enough for a cluster
