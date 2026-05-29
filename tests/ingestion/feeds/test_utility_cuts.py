import pytest
from unittest.mock import patch, MagicMock
from ingestion.feeds.utility_cuts import (
    classify_work_type,
    parse_permit,
    fetch_utility_cuts,
)
from specs.data_contracts import EventType, SourceFeed

MOCK_COORDS = (43.7115, -79.4317)
mock_geocode = lambda address: MOCK_COORDS


# --- classify_work_type ---

def test_classify_watermain():
    assert classify_work_type("Watermain Repair") == EventType.WATERMAIN_BREAK

def test_classify_sewer():
    assert classify_work_type("Sewer Replacement") == EventType.SEWER_BACKUP

def test_classify_default():
    assert classify_work_type("Sidewalk Work") == EventType.UTILITY_WORK

def test_classify_empty():
    assert classify_work_type("") == EventType.UTILITY_WORK


# --- parse_permit ---

def test_parse_permit_happy_path(raw_utility_cut):
    event = parse_permit(raw_utility_cut, geocode_fn=mock_geocode)
    assert event is not None
    assert event.source == SourceFeed.UTILITY_CUTS
    assert event.latitude == 43.7115

def test_parse_permit_no_geocode_returns_none(raw_utility_cut):
    result = parse_permit(raw_utility_cut, geocode_fn=None)
    assert result is None

def test_parse_permit_geocode_fails_returns_none(raw_utility_cut):
    result = parse_permit(raw_utility_cut, geocode_fn=lambda a: None)
    assert result is None

def test_parse_permit_missing_address():
    result = parse_permit({"permit_id": "x"}, geocode_fn=mock_geocode)
    assert result is None

def test_parse_permit_empty_dict():
    assert parse_permit({}, geocode_fn=mock_geocode) is None

def test_parse_permit_work_type_classified(raw_utility_cut):
    event = parse_permit(raw_utility_cut, geocode_fn=mock_geocode)
    assert event.event_type == EventType.WATERMAIN_BREAK


# --- fetch_utility_cuts ---

def test_fetch_returns_list_on_success(raw_utility_cut):
    mock_resp = MagicMock()
    mock_resp.json.return_value = [raw_utility_cut]
    mock_resp.raise_for_status = MagicMock()
    with patch("ingestion.feeds.utility_cuts.requests.get", return_value=mock_resp):
        results = fetch_utility_cuts(geocode_fn=mock_geocode)
    assert isinstance(results, list)

def test_fetch_returns_empty_on_network_failure():
    with patch("ingestion.feeds.utility_cuts.requests.get", side_effect=Exception("timeout")):
        results = fetch_utility_cuts()
    assert results == []

def test_fetch_handles_dict_response(raw_utility_cut):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": [raw_utility_cut]}
    mock_resp.raise_for_status = MagicMock()
    with patch("ingestion.feeds.utility_cuts.requests.get", return_value=mock_resp):
        results = fetch_utility_cuts(geocode_fn=mock_geocode)
    assert isinstance(results, list)
