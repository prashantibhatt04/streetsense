import pytest
from unittest.mock import patch
from datetime import datetime, timezone
from specs.data_contracts import (
    UnifiedEvent, SourceFeed, EventType,
    PredictedCascade, DispatchRecommendation,
)
from agents.prediction_agent import (
    affected_routes_from_address,
    build_prediction_prompt,
    parse_llm_response,
    predict_cascade,
    predict_batch,
    STREET_TO_ROUTES,
)

_NO_GTFS = patch("agents.prediction_agent._gtfs_routes_near", return_value=[])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event(event_type=EventType.WATERMAIN_BREAK, address="Bathurst St & Prue Ave, Toronto, ON",
               event_id="311-2024-bathurst-001", **overrides):
    base = dict(
        event_id=event_id,
        source=SourceFeed.REQUESTS_311,
        event_type=event_type,
        latitude=43.7115,
        longitude=-79.4317,
        address=address,
        description="Watermain-Possible Break reported",
        severity_raw=2,
        timestamp=datetime(2024, 10, 2, 8, 43, tzinfo=timezone.utc),
        source_id="src-001",
    )
    base.update(overrides)
    return UnifiedEvent(**base)


VALID_LLM_RESPONSE = {
    "predicted_impacts": [
        "Bathurst St likely closed northbound within 1 hour",
        "511 streetcar at risk of diversion via Davenport",
    ],
    "recommended_dispatches": [
        {
            "dispatch_type": "water_repair",
            "target_department": "Toronto Water",
            "message": "Dispatch crew to Bathurst & Prue — possible watermain break",
            "priority": "HIGH",
        },
        {
            "dispatch_type": "ttc_diversion",
            "target_department": "TTC Operations",
            "message": "Prepare 511 diversion plan via Davenport",
            "priority": "MEDIUM",
        },
    ],
    "confidence": 0.82,
    "reasoning": "Watermain breaks on Bathurst historically cascade to road closure + TTC diversion.",
}


# ---------------------------------------------------------------------------
# affected_routes_from_address
# ---------------------------------------------------------------------------

def test_affected_routes_bathurst():
    routes = affected_routes_from_address("Bathurst St & Prue Ave, Toronto, ON")
    assert any(r["route"] == "511" for r in routes)


def test_affected_routes_king():
    routes = affected_routes_from_address("King St W at Spadina Ave")
    route_ids = {r["route"] for r in routes}
    assert "504" in route_ids


def test_affected_routes_unknown_street():
    routes = affected_routes_from_address("Rosedale Valley Rd, Toronto, ON")
    assert routes == []


def test_affected_routes_case_insensitive():
    routes = affected_routes_from_address("BATHURST ST & PRUE AVE")
    assert any(r["route"] == "511" for r in routes)


def test_affected_routes_multiple_streets():
    routes = affected_routes_from_address("King St W at Bathurst St")
    route_ids = {r["route"] for r in routes}
    assert "504" in route_ids
    assert "511" in route_ids


# ---------------------------------------------------------------------------
# build_prediction_prompt
# ---------------------------------------------------------------------------

def test_prompt_contains_event_type():
    event = make_event()
    prompt = build_prediction_prompt(event)
    assert "watermain_break" in prompt


def test_prompt_contains_address():
    event = make_event()
    prompt = build_prediction_prompt(event)
    assert "Bathurst" in prompt


def test_prompt_contains_route_hint():
    # Keyword fallback: GTFS returns nothing, address keyword "bathurst" → route 511
    event = make_event(address="Bathurst St & Prue Ave, Toronto, ON")
    with _NO_GTFS:
        prompt = build_prediction_prompt(event)
    assert "511" in prompt


def test_prompt_no_route_hint_for_unknown_street():
    # GTFS and keyword both return nothing → fallback message
    event = make_event(address="Rosedale Valley Rd, Toronto, ON")
    with _NO_GTFS:
        prompt = build_prediction_prompt(event)
    assert "no known TTC route" in prompt


def test_prompt_uses_gtfs_routes_when_available():
    # GTFS routes are merged into the hint alongside any keyword matches.
    # "Don Valley Parkway / Bayview Ave" has no keyword match, so only GTFS route 28 appears.
    gtfs_routes = [{"route_id": "28", "short_name": "28", "long_name": "Bayview", "route_type": "bus"}]
    event = make_event(address="Don Valley Parkway / Bayview Ave")
    with patch("agents.prediction_agent._gtfs_routes_near", return_value=gtfs_routes):
        prompt = build_prediction_prompt(event)
    assert "28" in prompt
    assert "Bayview" in prompt


def test_prompt_flooding_includes_stormwater_dept_hint():
    event = make_event(event_type=EventType.FLOODING)
    with _NO_GTFS:
        prompt = build_prediction_prompt(event)
    assert "Stormwater" in prompt
    assert "Department guidance" in prompt


def test_prompt_sewer_backup_includes_wastewater_dept_hint():
    event = make_event(event_type=EventType.SEWER_BACKUP)
    with _NO_GTFS:
        prompt = build_prediction_prompt(event)
    assert "Wastewater" in prompt


def test_prompt_watermain_has_no_dept_hint():
    event = make_event(event_type=EventType.WATERMAIN_BREAK)
    with _NO_GTFS:
        prompt = build_prediction_prompt(event)
    assert "Department guidance" not in prompt


def test_prompt_excludes_night_routes_from_gtfs():
    # Night routes (300–399) must be filtered before being passed to the LLM
    gtfs_routes = [
        {"route_id": "501", "short_name": "501", "long_name": "Queen", "route_type": "streetcar"},
        {"route_id": "301", "short_name": "301", "long_name": "Queen Night", "route_type": "bus"},
    ]
    event = make_event(address="Queen St W & Claremont St")
    with patch("agents.prediction_agent._gtfs_routes_near", return_value=gtfs_routes):
        prompt = build_prediction_prompt(event)
    assert "501" in prompt
    assert "301" not in prompt


def test_prompt_contains_json_schema():
    event = make_event()
    prompt = build_prediction_prompt(event)
    assert "predicted_impacts" in prompt
    assert "recommended_dispatches" in prompt
    assert "confidence" in prompt


# ---------------------------------------------------------------------------
# parse_llm_response
# ---------------------------------------------------------------------------

def test_parse_valid_response():
    result = parse_llm_response(VALID_LLM_RESPONSE, "311-test-001")
    assert result is not None
    assert result.trigger_event_id == "311-test-001"
    assert result.confidence == 0.82
    assert len(result.predicted_impacts) == 2
    assert len(result.recommended_dispatches) == 2


def test_parse_clamps_confidence_high():
    raw = {**VALID_LLM_RESPONSE, "confidence": 1.5}
    result = parse_llm_response(raw, "311-test-001")
    assert result.confidence == 1.0


def test_parse_clamps_confidence_low():
    raw = {**VALID_LLM_RESPONSE, "confidence": -0.5}
    result = parse_llm_response(raw, "311-test-001")
    assert result.confidence == 0.0


def test_parse_invalid_dispatch_type_falls_back():
    raw = {
        **VALID_LLM_RESPONSE,
        "recommended_dispatches": [
            {"dispatch_type": "laser_blast", "target_department": "TTC",
             "message": "test", "priority": "HIGH"},
        ],
    }
    result = parse_llm_response(raw, "311-test-001")
    assert result.recommended_dispatches[0].dispatch_type == "notify_department"


def test_parse_invalid_priority_falls_back():
    raw = {
        **VALID_LLM_RESPONSE,
        "recommended_dispatches": [
            {"dispatch_type": "water_repair", "target_department": "Toronto Water",
             "message": "test", "priority": "URGENT"},
        ],
    }
    result = parse_llm_response(raw, "311-test-001")
    assert result.recommended_dispatches[0].priority == "MEDIUM"


def test_parse_empty_dispatches_ok():
    raw = {**VALID_LLM_RESPONSE, "recommended_dispatches": []}
    result = parse_llm_response(raw, "311-test-001")
    assert result.recommended_dispatches == []


def test_parse_missing_impacts_ok():
    raw = {**VALID_LLM_RESPONSE, "predicted_impacts": None}
    result = parse_llm_response(raw, "311-test-001")
    assert result.predicted_impacts == []


def test_parse_dispatch_ids_are_unique():
    result = parse_llm_response(VALID_LLM_RESPONSE, "311-test-001")
    ids = [d.dispatch_id for d in result.recommended_dispatches]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# predict_cascade — non-triggering types return None
# ---------------------------------------------------------------------------

def test_predict_returns_none_for_road_closure():
    event = make_event(event_type=EventType.ROAD_CLOSURE)
    assert predict_cascade(event) is None


def test_predict_returns_none_for_transit_disruption():
    event = make_event(event_type=EventType.TRANSIT_DISRUPTION)
    assert predict_cascade(event) is None


def test_predict_returns_none_for_utility_work():
    event = make_event(event_type=EventType.UTILITY_WORK)
    assert predict_cascade(event) is None


def test_predict_sewer_backup_triggers_cascade():
    event = make_event(event_type=EventType.SEWER_BACKUP)
    with patch("agents.prediction_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = predict_cascade(event)
    assert result is not None
    assert isinstance(result, PredictedCascade)


# ---------------------------------------------------------------------------
# predict_cascade — watermain_break happy path
# ---------------------------------------------------------------------------

def test_predict_watermain_happy_path():
    event = make_event(event_type=EventType.WATERMAIN_BREAK)
    with patch("agents.prediction_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = predict_cascade(event)
    assert result is not None
    assert isinstance(result, PredictedCascade)
    assert result.trigger_event_id == event.event_id
    assert result.confidence == 0.82
    assert len(result.recommended_dispatches) >= 1


def test_predict_flooding_happy_path():
    event = make_event(event_type=EventType.FLOODING,
                       description="Storm flooding on Bathurst")
    with patch("agents.prediction_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = predict_cascade(event)
    assert result is not None


# ---------------------------------------------------------------------------
# predict_cascade — LLM failure falls back to heuristic
# ---------------------------------------------------------------------------

def test_predict_llm_failure_returns_heuristic():
    event = make_event()
    with patch("agents.prediction_agent.call_llm_json", return_value={}):
        result = predict_cascade(event)
    assert result is not None
    assert result.confidence == 0.5
    assert "Heuristic" in result.reasoning


def test_predict_llm_exception_returns_heuristic():
    event = make_event()
    with patch("agents.prediction_agent.call_llm_json", side_effect=Exception("timeout")):
        result = predict_cascade(event)
    assert result is not None
    assert isinstance(result, PredictedCascade)


def test_predict_heuristic_includes_water_repair_dispatch():
    event = make_event()
    with patch("agents.prediction_agent.call_llm_json", return_value={}):
        result = predict_cascade(event)
    types = {d.dispatch_type for d in result.recommended_dispatches}
    assert "water_repair" in types


def test_predict_heuristic_includes_ttc_diversion_for_known_street():
    event = make_event(address="Bathurst St & Prue Ave, Toronto, ON")
    with patch("agents.prediction_agent.call_llm_json", return_value={}):
        result = predict_cascade(event)
    types = {d.dispatch_type for d in result.recommended_dispatches}
    assert "ttc_diversion" in types


def test_predict_heuristic_no_ttc_for_unknown_street():
    event = make_event(address="Rosedale Valley Rd, Toronto, ON")
    with patch("agents.prediction_agent.call_llm_json", return_value={}), _NO_GTFS:
        result = predict_cascade(event)
    types = {d.dispatch_type for d in result.recommended_dispatches}
    assert "ttc_diversion" not in types


def test_predict_never_raises_on_bad_llm():
    event = make_event()
    with patch("agents.prediction_agent.call_llm_json", side_effect=RuntimeError("crash")):
        result = predict_cascade(event)
    assert result is not None


# ---------------------------------------------------------------------------
# Integration: single 311 event → predicted cascade with ≥1 dispatch
# ---------------------------------------------------------------------------

def test_integration_single_311_watermain_produces_dispatches(bathurst_watermain_event):
    """Feed the first oct2024_bathurst event alone — must return ≥1 dispatch recommendation."""
    with patch("agents.prediction_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = predict_cascade(bathurst_watermain_event)
    assert result is not None
    assert len(result.recommended_dispatches) >= 1
    assert all(isinstance(d, DispatchRecommendation) for d in result.recommended_dispatches)


def test_integration_dispatch_statuses_default_awaiting(bathurst_watermain_event):
    with patch("agents.prediction_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = predict_cascade(bathurst_watermain_event)
    for d in result.recommended_dispatches:
        assert d.status == "AWAITING_APPROVAL"


# ---------------------------------------------------------------------------
# predict_batch
# ---------------------------------------------------------------------------

def test_predict_batch_empty():
    assert predict_batch([]) == []


def test_predict_batch_skips_non_triggering(
    bathurst_watermain_event, bathurst_road_closure_event, bathurst_ttc_alert_event
):
    events = [bathurst_watermain_event, bathurst_road_closure_event, bathurst_ttc_alert_event]
    with patch("agents.prediction_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        results = predict_batch(events)
    # Only watermain_break triggers a prediction
    assert len(results) == 1
    assert results[0].trigger_event_id == bathurst_watermain_event.event_id


def test_predict_batch_multiple_triggering_events():
    events = [
        make_event(event_id=f"311-{i}", event_type=EventType.WATERMAIN_BREAK)
        for i in range(3)
    ]
    with patch("agents.prediction_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        results = predict_batch(events)
    assert len(results) == 3
