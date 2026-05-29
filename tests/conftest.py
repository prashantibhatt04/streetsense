"""
Shared pytest fixtures for StreetSense test suite.
All test data lives here. Never duplicate fixtures across test files.
"""

import pytest
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Raw feed payloads (pre-parsing, intentionally messy)
# ---------------------------------------------------------------------------

@pytest.fixture
def raw_road_restriction():
    """
    A single raw road restriction dict as returned by the Toronto feed
    after the backslash-escape fix is applied. Contains all expected fields.
    """
    return {
        "id": "RR-2024-88421",
        "road_class": "Collector",
        "location": "Bathurst St from Prue Ave to Glencairn Ave",
        "from_street": "Prue Ave",
        "to_street": "Glencairn Ave",
        "work_type": "Emergency - Watermain Break",
        "contractor": "City of Toronto - Water Services",
        "start_date": "2024-10-02",
        "end_date": "2024-10-04",
        "latitude": 43.7115,
        "longitude": -79.4317,
    }


@pytest.fixture
def malformed_road_restriction():
    """
    Raw dict simulating the Toronto feed's known backslash-escape bug.
    The description field contains invalid escape sequences that break
    standard json.loads() — the ingestion layer must handle this.
    """
    return {
        "id": "RR-2024-99999",
        "location": "King St \\W at Spadina",   # invalid \W escape
        "latitude": "not_a_float",              # wrong type
        "longitude": None,                      # missing
        "work_type": "",                        # empty string
    }


@pytest.fixture
def raw_ttc_alert():
    """
    A single TTC alert dict as it comes out of the GTFS-RT text parser.
    """
    return {
        "alert_id": "ttc-alert-511-20241002",
        "route_id": "511",
        "route_name": "Bathurst",
        "stop_id": "14321",
        "header": "511 Bathurst Streetcar - Delays",
        "description": (
            "Due to a watermain break at Bathurst and Prue, "
            "511 Bathurst streetcars are diverting via Davenport. "
            "Expect 15-20 minute delays."
        ),
        "severity": "WARNING",
        "timestamp": "2024-10-02T09:15:00Z",
    }


@pytest.fixture
def raw_utility_cut():
    """
    A single utility cut permit dict as returned by the Toronto open data feed.
    No lat/lng — geocoding is required before this can become a UnifiedEvent.
    """
    return {
        "permit_id": "UC-2024-033871",
        "address": "Bathurst St & Prue Ave, Toronto, ON",
        "client_name": "Toronto Water",
        "work_type": "Watermain Repair",
        "start_date": "2024-10-02",
        "end_date": "2024-10-05",
        "status": "Active",
    }


@pytest.fixture
def raw_311_request():
    """
    A single 311 service request row as parsed from the CSV.
    Intersection strings require geocoding. No lat/lng present.
    """
    return {
        "Creation Date": "2024-10-02T08:43:00",
        "Service Request Type": "Watermain-Possible Break",
        "Intersection Street 1": "Bathurst St",
        "Intersection Street 2": "Prue Ave",
        "Ward": "8",
        "Status": "Open",
        "Service Request #": "88001",
    }

# ---------------------------------------------------------------------------
# UnifiedEvent fixtures (post-normalisation, fully typed)
# ---------------------------------------------------------------------------

@pytest.fixture
def bathurst_watermain_event():
    """
    Single watermain break event at Bathurst & Prue Ave, Oct 2 2024.
    Represents a 311 service request that has been geocoded and normalised
    into a UnifiedEvent. Used as the atomic building block for cluster tests.
    """
    from specs.data_contracts import UnifiedEvent, SourceFeed, EventType

    return UnifiedEvent(
        event_id="311-2024-bathurst-prue-001",
        source=SourceFeed.REQUESTS_311,
        event_type=EventType.WATERMAIN_BREAK,
        latitude=43.7115,
        longitude=-79.4317,
        address="Bathurst St & Prue Ave, Toronto, ON",
        description="Watermain-Possible Break reported at Bathurst & Prue Ave",
        severity_raw=2,
        timestamp=datetime(2024, 10, 2, 8, 43, 0, tzinfo=timezone.utc),
        source_id="311-88001",
        metadata={"ward": "8", "status": "Open"},
    )


@pytest.fixture
def bathurst_road_closure_event():
    """
    Road closure on Bathurst St caused by the watermain break.
    Source: road restrictions feed. Has native lat/lng.
    """
    from specs.data_contracts import UnifiedEvent, SourceFeed, EventType

    return UnifiedEvent(
        event_id="RR-2024-88421",
        source=SourceFeed.ROAD_RESTRICTIONS,
        event_type=EventType.ROAD_CLOSURE,
        latitude=43.7118,
        longitude=-79.4315,
        address="Bathurst St from Prue Ave to Glencairn Ave",
        description="Emergency - Watermain Break: Bathurst St closed northbound",
        severity_raw=3,
        timestamp=datetime(2024, 10, 2, 9, 0, 0, tzinfo=timezone.utc),
        source_id="RR-2024-88421",
        metadata={"contractor": "City of Toronto - Water Services"},
    )


@pytest.fixture
def bathurst_ttc_alert_event():
    """
    TTC 511 Bathurst streetcar delay caused by the road closure.
    Source: TTC alerts feed.
    """
    from specs.data_contracts import UnifiedEvent, SourceFeed, EventType

    return UnifiedEvent(
        event_id="ttc-alert-511-20241002",
        source=SourceFeed.TTC_ALERTS,
        event_type=EventType.TRANSIT_DISRUPTION,
        latitude=43.7120,
        longitude=-79.4310,
        address="Bathurst St at Prue Ave",
        description="511 Bathurst Streetcar diverting via Davenport due to watermain break",
        severity_raw=2,
        timestamp=datetime(2024, 10, 2, 9, 15, 0, tzinfo=timezone.utc),
        source_id="ttc-alert-511-20241002",
        metadata={"route_id": "511", "stop_id": "14321"},
    )


# ---------------------------------------------------------------------------
# Cluster and correlation fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bathurst_cluster(
    bathurst_watermain_event,
    bathurst_road_closure_event,
    bathurst_ttc_alert_event,
):
    """
    Three events forming the Oct 2 2024 Bathurst cascade.
    Watermain break → road closure → streetcar disruption.
    This is the primary demo scenario.
    """
    from specs.data_contracts import ClusterCandidate

    return ClusterCandidate(
        cluster_id="cluster-bathurst-20241002-001",
        events=[
            bathurst_watermain_event,
            bathurst_road_closure_event,
            bathurst_ttc_alert_event,
        ],
        centroid_lat=43.7118,
        centroid_lng=-79.4314,
        radius_metres=150.0,
        time_window_minutes=32,
    )


@pytest.fixture
def causal_correlation(bathurst_cluster):
    """
    A CorrelationResult where is_causal=True, confidence=0.87.
    Represents the agent's conclusion that the watermain break caused
    the road closure which caused the streetcar disruption.
    """
    from specs.data_contracts import CorrelationResult

    return CorrelationResult(
        cluster_id=bathurst_cluster.cluster_id,
        is_causal=True,
        confidence=0.87,
        cascade_type="watermain_to_road_to_ttc",
        causal_chain=[
            "Watermain break reported at Bathurst & Prue (08:43)",
            "Emergency road closure issued for same block (09:00)",
            "511 Bathurst streetcar diverted due to closure (09:15)",
        ],
        reasoning=(
            "Three events on the same block within 32 minutes. "
            "Work type 'Watermain Break' in road closure matches 311 report type. "
            "TTC alert explicitly references watermain break as cause."
        ),
        llm_model="qwen2.5:14b",
    )


# ---------------------------------------------------------------------------
# Adversarial fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mississauga_event():
    """
    Event with coordinates outside Toronto bounds.
    Should be rejected by the geo validation layer.
    Lat/lng is Mississauga City Hall.
    """
    return {
        "event_id": "test-out-of-bounds",
        "latitude": 43.10,
        "longitude": -79.4317,
        "address": "300 City Centre Dr, Mississauga, ON",
        "description": "This should be rejected — outside Toronto bbox",
    }


@pytest.fixture
def single_event_cluster(bathurst_watermain_event):
    """
    A cluster with only one event — not enough to be causal.
    The correlation agent must return is_causal=False for this.
    """
    from specs.data_contracts import ClusterCandidate

    return ClusterCandidate(
        cluster_id="cluster-single-event",
        events=[bathurst_watermain_event],
        centroid_lat=bathurst_watermain_event.latitude,
        centroid_lng=bathurst_watermain_event.longitude,
        radius_metres=0.0,
        time_window_minutes=0,
    )


# ---------------------------------------------------------------------------
# Infrastructure fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db(tmp_path):
    """
    Returns a path to a fresh temporary SQLite database.
    Deleted automatically after each test by pytest's tmp_path fixture.
    """
    return tmp_path / "test_streetsense.db"
