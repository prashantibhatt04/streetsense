import pytest
from unittest.mock import patch, MagicMock
from ingestion.feeds.requests_311 import (
    classify_request_type,
    build_address,
    parse_row,
    parse_csv_bytes,
    fetch_311_requests,
)
from specs.data_contracts import EventType, SourceFeed

MOCK_COORDS = (43.7115, -79.4317)
mock_geocode = lambda address: MOCK_COORDS


# --- classify_request_type ---

def test_classify_watermain():
    assert classify_request_type("Watermain-Possible Break") == EventType.WATERMAIN_BREAK

def test_classify_flooding():
    assert classify_request_type("Storm Event-Flooding") == EventType.FLOODING

def test_classify_unknown_returns_none():
    assert classify_request_type("Noise Complaint") is None

def test_classify_empty_returns_none():
    assert classify_request_type("") is None


# --- build_address ---

def test_build_address_both_streets():
    assert build_address("Bathurst St", "Prue Ave") == "Bathurst St & Prue Ave, Toronto, ON"

def test_build_address_one_street():
    assert build_address("Bathurst St", "") == "Bathurst St, Toronto, ON"

def test_build_address_no_streets():
    assert build_address("", "") is None

def test_build_address_none_values():
    assert build_address(None, None) is None


# --- parse_row ---

def test_parse_row_happy_path(raw_311_request):
    event = parse_row(raw_311_request, geocode_fn=mock_geocode)
    assert event is not None
    assert event.source == SourceFeed.REQUESTS_311
    assert event.event_type == EventType.WATERMAIN_BREAK

def test_parse_row_non_water_type_skipped():
    row = {"Service Request Type": "Noise Complaint", "Intersection Street 1": "Bathurst St"}
    assert parse_row(row, geocode_fn=mock_geocode) is None

def test_parse_row_no_geocode_returns_none(raw_311_request):
    assert parse_row(raw_311_request, geocode_fn=None) is None

def test_parse_row_geocode_fails_returns_none(raw_311_request):
    assert parse_row(raw_311_request, geocode_fn=lambda a: None) is None

def test_parse_row_missing_address_returns_none():
    row = {"Service Request Type": "Watermain-Possible Break"}
    assert parse_row(row, geocode_fn=mock_geocode) is None

def test_parse_row_bad_date_uses_now(raw_311_request):
    row = {**raw_311_request, "creation_date": "not-a-date"}
    event = parse_row(row, geocode_fn=mock_geocode)
    assert event is not None

def test_parse_row_empty_dict():
    assert parse_row({}, geocode_fn=mock_geocode) is None


# --- parse_csv_bytes ---

def test_parse_csv_bytes_valid():
    csv_data = b"Service Request Type,Intersection Street 1\nWatermain-Possible Break,Bathurst St\n"
    rows = parse_csv_bytes(csv_data)
    assert len(rows) == 1
    assert rows[0]["Service Request Type"] == "Watermain-Possible Break"

def test_parse_csv_bytes_empty():
    rows = parse_csv_bytes(b"")
    assert rows == []


# --- fetch_311_requests ---

def test_fetch_returns_empty_on_network_failure():
    with patch("ingestion.feeds.requests_311.requests.get", side_effect=Exception("timeout")):
        results = fetch_311_requests()
    assert results == []

def test_fetch_parses_plain_csv(raw_311_request):
    csv_bytes = (
        b"Service Request Type,Intersection Street 1,Intersection Street 2,Creation Date,Status,Ward\n"
        b"Watermain-Possible Break,Bathurst St,Prue Ave,2024-10-02T08:43:00,Open,8\n"
    )
    mock_resp = MagicMock()
    mock_resp.content = csv_bytes
    mock_resp.raise_for_status = MagicMock()
    with patch("ingestion.feeds.requests_311.requests.get", return_value=mock_resp):
        results = fetch_311_requests(geocode_fn=mock_geocode)
    assert len(results) == 1
