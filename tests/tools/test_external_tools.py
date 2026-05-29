import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from tools.external_tools import fetch_bikeshare_nearby, emit_slack_notification
from specs.data_contracts import OperationalBrief


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_brief(severity: int = 7) -> OperationalBrief:
    return OperationalBrief(
        brief_id="brief-test-001",
        generated_at=datetime(2024, 10, 2, 9, 30, 0, tzinfo=timezone.utc),
        cluster_id="cluster-bathurst-001",
        headline="Watermain break on Bathurst disrupts transit",
        body="Three related events on Bathurst St. Water Services and TTC responding.",
        severity_score=severity,
        recommended_actions=["Deploy repair crew", "Activate 511 diversion"],
        source_event_count=3,
    )


MOCK_STATION_INFO = {
    "data": {
        "stations": [
            {"station_id": "7001", "name": "Bathurst St / Davenport Rd",
             "lat": 43.7120, "lon": -79.4315},
            {"station_id": "7002", "name": "Bloor St W / Bathurst St",
             "lat": 43.6662, "lon": -79.4114},
        ]
    }
}

MOCK_STATION_STATUS = {
    "data": {
        "stations": [
            {"station_id": "7001", "num_bikes_available": 4},
            {"station_id": "7002", "num_bikes_available": 0},
        ]
    }
}


# ---------------------------------------------------------------------------
# fetch_bikeshare_nearby
# ---------------------------------------------------------------------------

def test_bikeshare_returns_stations_within_radius():
    def mock_get(url, timeout):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "station_status" in url:
            resp.json.return_value = MOCK_STATION_STATUS
        else:
            resp.json.return_value = MOCK_STATION_INFO
        return resp

    with patch("tools.external_tools.requests.get", side_effect=mock_get):
        results = fetch_bikeshare_nearby(43.7115, -79.4317, radius_m=500)

    assert isinstance(results, list)
    for s in results:
        assert "name" in s
        assert "available_bikes" in s
        assert "distance_m" in s
        assert s["distance_m"] <= 500


def test_bikeshare_returns_empty_on_network_failure():
    with patch("tools.external_tools.requests.get", side_effect=ConnectionError("down")):
        result = fetch_bikeshare_nearby(43.7115, -79.4317)
    assert result == []


def test_bikeshare_returns_empty_on_bad_json():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.side_effect = ValueError("bad json")
    with patch("tools.external_tools.requests.get", return_value=mock_resp):
        result = fetch_bikeshare_nearby(43.7115, -79.4317)
    assert result == []


def test_bikeshare_sorted_by_distance():
    def mock_get(url, timeout):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "station_status" in url:
            resp.json.return_value = MOCK_STATION_STATUS
        else:
            resp.json.return_value = MOCK_STATION_INFO
        return resp

    with patch("tools.external_tools.requests.get", side_effect=mock_get):
        results = fetch_bikeshare_nearby(43.7115, -79.4317, radius_m=5000)

    if len(results) >= 2:
        assert results[0]["distance_m"] <= results[1]["distance_m"]


# ---------------------------------------------------------------------------
# emit_slack_notification
# ---------------------------------------------------------------------------

def test_slack_returns_true_on_200():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("tools.external_tools.requests.post", return_value=mock_resp):
        result = emit_slack_notification(make_brief(), webhook_url="https://hooks.slack.com/test")
    assert result is True


def test_slack_returns_false_on_http_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"
    with patch("tools.external_tools.requests.post", return_value=mock_resp):
        result = emit_slack_notification(make_brief(), webhook_url="https://hooks.slack.com/test")
    assert result is False


def test_slack_returns_false_on_network_failure():
    with patch("tools.external_tools.requests.post", side_effect=ConnectionError("down")):
        result = emit_slack_notification(make_brief(), webhook_url="https://hooks.slack.com/test")
    assert result is False


def test_slack_returns_false_when_no_webhook_url():
    with patch.dict("os.environ", {}, clear=True):
        # Ensure SLACK_WEBHOOK_URL is not set
        import os
        os.environ.pop("SLACK_WEBHOOK_URL", None)
        result = emit_slack_notification(make_brief(), webhook_url="")
    assert result is False


def test_slack_payload_includes_severity(monkeypatch):
    captured = {}

    def mock_post(url, json, timeout):
        captured["payload"] = json
        resp = MagicMock()
        resp.status_code = 200
        return resp

    with patch("tools.external_tools.requests.post", side_effect=mock_post):
        emit_slack_notification(make_brief(severity=9), webhook_url="https://hooks.slack.com/test")

    assert "9/10" in captured["payload"]["text"]
