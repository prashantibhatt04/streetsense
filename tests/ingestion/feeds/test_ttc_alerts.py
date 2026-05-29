import pytest
from unittest.mock import patch, MagicMock
from ingestion.feeds.ttc_alerts import (
    parse_severity,
    route_to_coords,
    parse_gtfsrt_text,
    parse_alert,
    fetch_ttc_alerts,
    DEFAULT_TTC_COORD,
)
from specs.data_contracts import EventType, SourceFeed


SAMPLE_GTFSRT = """alert_id: ttc-alert-511-20241002
route_id: 511
severity: WARNING
header: 511 Bathurst Streetcar - Delays
description: Diverting via Davenport due to watermain break

alert_id: ttc-alert-504-20241002
route_id: 504
severity: INFO
header: 504 King - Minor Delays
description: Minor delays near Spadina
"""


# --- parse_severity ---

def test_severity_warning():
    assert parse_severity("WARNING") == 2

def test_severity_severe():
    assert parse_severity("SEVERE") == 4

def test_severity_unknown():
    assert parse_severity("GARBAGE") == 1

def test_severity_case_insensitive():
    assert parse_severity("warning") == 2


# --- route_to_coords ---

def test_known_route_511():
    lat, lng = route_to_coords("511")
    assert lat == 43.7120

def test_unknown_route_returns_default():
    assert route_to_coords("999") == DEFAULT_TTC_COORD


# --- parse_gtfsrt_text ---

def test_parse_two_alerts():
    alerts = parse_gtfsrt_text(SAMPLE_GTFSRT)
    assert len(alerts) == 2

def test_parse_fields_correct():
    alerts = parse_gtfsrt_text(SAMPLE_GTFSRT)
    assert alerts[0]["route_id"] == "511"
    assert alerts[0]["severity"] == "WARNING"

def test_parse_empty_string():
    assert parse_gtfsrt_text("") == []

def test_parse_no_blank_line_separator():
    single = "alert_id: x\nroute_id: 511\nseverity: INFO\n"
    alerts = parse_gtfsrt_text(single)
    assert len(alerts) == 1


# --- parse_alert ---

def test_parse_alert_happy_path(raw_ttc_alert):
    event = parse_alert(raw_ttc_alert)
    assert event is not None
    assert event.source == SourceFeed.TTC_ALERTS
    assert event.event_type == EventType.TRANSIT_DISRUPTION

def test_parse_alert_known_route_coords(raw_ttc_alert):
    event = parse_alert(raw_ttc_alert)
    assert event.latitude == 43.7120

def test_parse_alert_empty_dict():
    event = parse_alert({})
    assert event is not None  # falls back to defaults, should not crash

def test_parse_alert_unknown_route():
    event = parse_alert({"route_id": "999", "alert_id": "x"})
    assert event.latitude == DEFAULT_TTC_COORD[0]


# --- fetch_ttc_alerts ---

def test_fetch_returns_list_on_success():
    mock_resp = MagicMock()
    mock_resp.text = SAMPLE_GTFSRT
    mock_resp.raise_for_status = MagicMock()
    with patch("ingestion.feeds.ttc_alerts.requests.get", return_value=mock_resp):
        results = fetch_ttc_alerts()
    assert len(results) == 2

def test_fetch_returns_empty_on_network_failure():
    with patch("ingestion.feeds.ttc_alerts.requests.get", side_effect=Exception("timeout")):
        results = fetch_ttc_alerts()
    assert results == []
