import pytest
from unittest.mock import patch, MagicMock
from ingestion.feeds.road_restrictions import (
    fix_backslash_escapes,
    classify_work_type,
    is_within_toronto,
    parse_restriction,
    fetch_road_restrictions,
)
from specs.data_contracts import EventType


# --- fix_backslash_escapes ---

def test_fix_backslash_valid_escapes_untouched():
    raw = '{"a": "line1\\nline2"}'
    assert fix_backslash_escapes(raw) == raw

def test_fix_backslash_invalid_escape_fixed():
    raw = '{"location": "King St \\W at Spadina"}'
    fixed = fix_backslash_escapes(raw)
    import json
    parsed = json.loads(fixed)
    assert "King St" in parsed["location"]

def test_fix_backslash_empty_string():
    assert fix_backslash_escapes("") == ""


# --- classify_work_type ---

def test_classify_watermain():
    assert classify_work_type("Emergency - Watermain Break") == EventType.WATERMAIN_BREAK

def test_classify_road_closure():
    assert classify_work_type("Road Closure - Permit") == EventType.ROAD_CLOSURE

def test_classify_unknown():
    assert classify_work_type("Landscaping") == EventType.UNKNOWN

def test_classify_empty():
    assert classify_work_type("") == EventType.UNKNOWN


# --- is_within_toronto ---

def test_within_toronto_valid(raw_road_restriction):
    assert is_within_toronto(raw_road_restriction["latitude"], raw_road_restriction["longitude"])

def test_outside_toronto(mississauga_event):
    assert not is_within_toronto(mississauga_event["latitude"], mississauga_event["longitude"])


# --- parse_restriction ---

def test_parse_restriction_happy_path(raw_road_restriction):
    event = parse_restriction(raw_road_restriction)
    assert event is not None
    assert event.event_type == EventType.WATERMAIN_BREAK
    assert event.latitude == 43.7115

def test_parse_restriction_missing_lat_returns_none(malformed_road_restriction):
    result = parse_restriction(malformed_road_restriction)
    assert result is None

def test_parse_restriction_outside_toronto_returns_none(mississauga_event):
    result = parse_restriction({**mississauga_event, "id": "x", "work_type": "test"})
    assert result is None

def test_parse_restriction_empty_dict():
    assert parse_restriction({}) is None


# --- fetch_road_restrictions (network mocked) ---

def test_fetch_returns_list_on_success(raw_road_restriction):
    mock_resp = MagicMock()
    mock_resp.text = '[{"id":"1","latitude":43.7115,"longitude":-79.4317,"work_type":"Watermain","location":"Bathurst"}]'
    mock_resp.raise_for_status = MagicMock()
    with patch("ingestion.feeds.road_restrictions.requests.get", return_value=mock_resp):
        results = fetch_road_restrictions()
    assert isinstance(results, list)

def test_fetch_returns_empty_list_on_network_failure():
    with patch("ingestion.feeds.road_restrictions.requests.get", side_effect=Exception("timeout")):
        results = fetch_road_restrictions()
    assert results == []

def test_fetch_returns_empty_list_on_bad_json():
    mock_resp = MagicMock()
    mock_resp.text = "not json {{{"
    mock_resp.raise_for_status = MagicMock()
    with patch("ingestion.feeds.road_restrictions.requests.get", return_value=mock_resp):
        results = fetch_road_restrictions()
    assert results == []
