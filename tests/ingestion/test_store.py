import pytest
from ingestion.store import write_events, read_events, count_events
from specs.data_contracts import WriteResult


# --- write_events ---

def test_write_single_event(mock_db, bathurst_watermain_event):
    result = write_events([bathurst_watermain_event], mock_db)
    assert result.success_count == 1
    assert result.failure_count == 0


def test_write_multiple_events(mock_db, bathurst_watermain_event, bathurst_road_closure_event):
    result = write_events([bathurst_watermain_event, bathurst_road_closure_event], mock_db)
    assert result.success_count == 2
    assert result.failure_count == 0


def test_write_empty_list(mock_db):
    result = write_events([], mock_db)
    assert result.success_count == 0
    assert result.failure_count == 0


def test_write_idempotent(mock_db, bathurst_watermain_event):
    write_events([bathurst_watermain_event], mock_db)
    result = write_events([bathurst_watermain_event], mock_db)
    assert result.success_count == 1
    assert count_events(mock_db) == 1


def test_write_returns_write_result(mock_db, bathurst_watermain_event):
    result = write_events([bathurst_watermain_event], mock_db)
    assert isinstance(result, WriteResult)


# --- read_events ---

def test_read_returns_written_events(mock_db, bathurst_watermain_event):
    write_events([bathurst_watermain_event], mock_db)
    events = read_events(mock_db)
    assert len(events) == 1
    assert events[0].event_id == bathurst_watermain_event.event_id


def test_read_empty_db(mock_db):
    assert read_events(mock_db) == []


def test_read_limit_respected(mock_db, bathurst_watermain_event, bathurst_road_closure_event, bathurst_ttc_alert_event):
    write_events([bathurst_watermain_event, bathurst_road_closure_event, bathurst_ttc_alert_event], mock_db)
    events = read_events(mock_db, limit=2)
    assert len(events) == 2


def test_read_bad_path_returns_empty(tmp_path):
    bad_path = tmp_path / "nonexistent" / "db.sqlite"
    result = read_events(bad_path)
    assert result == []


# --- count_events ---

def test_count_empty_db(mock_db):
    assert count_events(mock_db) == 0


def test_count_after_write(mock_db, bathurst_cluster):
    write_events(bathurst_cluster.events, mock_db)
    assert count_events(mock_db) == 3
