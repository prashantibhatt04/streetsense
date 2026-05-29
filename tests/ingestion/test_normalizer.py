import pytest
from datetime import datetime, timezone
from ingestion.normalizer import (
    normalize_severity,
    normalize_timestamp,
    normalize_description,
    normalize_event,
    normalize_batch,
)
from specs.data_contracts import EventType


# --- normalize_severity ---

def test_severity_watermain_blended():
    # base=3, raw=3 -> blended=3
    assert normalize_severity(EventType.WATERMAIN_BREAK, 3) == 3

def test_severity_clamped_high():
    assert normalize_severity(EventType.FLOODING, 5) <= 5

def test_severity_clamped_low():
    assert normalize_severity(EventType.UNKNOWN, 0) >= 0

def test_severity_unknown_type():
    # base=1, raw=0 -> blended = round(0.5) = 0 (Python banker's rounding)
    assert normalize_severity(EventType.UNKNOWN, 0) == 0


# --- normalize_timestamp ---

def test_timestamp_naive_becomes_utc():
    naive = datetime(2024, 10, 2, 8, 0, 0)
    result = normalize_timestamp(naive)
    assert result.tzinfo == timezone.utc

def test_timestamp_aware_unchanged():
    aware = datetime(2024, 10, 2, 8, 0, 0, tzinfo=timezone.utc)
    assert normalize_timestamp(aware) == aware

def test_timestamp_none_returns_now():
    result = normalize_timestamp(None)
    assert result.tzinfo == timezone.utc

def test_timestamp_valid_string():
    result = normalize_timestamp("2024-10-02T08:43:00")
    assert result.year == 2024

def test_timestamp_bad_string_returns_now():
    result = normalize_timestamp("not-a-date")
    assert result.tzinfo == timezone.utc


# --- normalize_description ---

def test_description_non_empty_unchanged():
    assert normalize_description("Watermain break", EventType.WATERMAIN_BREAK) == "Watermain break"

def test_description_empty_uses_event_type():
    result = normalize_description("", EventType.WATERMAIN_BREAK)
    assert result == "Watermain Break"

def test_description_none_uses_event_type():
    result = normalize_description(None, EventType.ROAD_CLOSURE)
    assert result == "Road Closure"

def test_description_whitespace_uses_event_type():
    result = normalize_description("   ", EventType.FLOODING)
    assert result == "Flooding"


# --- normalize_event ---

def test_normalize_event_returns_unified_event(bathurst_watermain_event):
    from specs.data_contracts import UnifiedEvent
    result = normalize_event(bathurst_watermain_event)
    assert isinstance(result, UnifiedEvent)

def test_normalize_event_severity_blended(bathurst_watermain_event):
    result = normalize_event(bathurst_watermain_event)
    assert 0 <= result.severity_raw <= 5

def test_normalize_event_timestamp_utc(bathurst_watermain_event):
    result = normalize_event(bathurst_watermain_event)
    assert result.timestamp.tzinfo == timezone.utc

def test_normalize_event_description_non_empty(bathurst_watermain_event):
    result = normalize_event(bathurst_watermain_event)
    assert len(result.description) > 0


# --- normalize_batch ---

def test_normalize_batch_empty():
    assert normalize_batch([]) == []

def test_normalize_batch_all_returned(bathurst_watermain_event, bathurst_road_closure_event):
    results = normalize_batch([bathurst_watermain_event, bathurst_road_closure_event])
    assert len(results) == 2

def test_normalize_batch_never_raises(bathurst_watermain_event):
    # Should not raise even on repeated calls
    results = normalize_batch([bathurst_watermain_event] * 10)
    assert len(results) == 10
