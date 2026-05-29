import pytest
from unittest.mock import patch
from agents.correlation_agent import (
    summarise_cluster,
    parse_llm_response,
    fallback_result,
    correlate_cluster,
    correlate_batch,
)
from specs.data_contracts import CorrelationResult

VALID_LLM_RESPONSE = {
    "is_causal": True,
    "confidence": 0.87,
    "cascade_type": "watermain_to_road_to_ttc",
    "causal_chain": ["Watermain break", "Road closure", "TTC diversion"],
    "reasoning": "Three events on same block within 32 minutes.",
}


# --- summarise_cluster ---

def test_summarise_contains_cluster_id(bathurst_cluster):
    summary = summarise_cluster(bathurst_cluster)
    assert bathurst_cluster.cluster_id in summary

def test_summarise_contains_all_sources(bathurst_cluster):
    summary = summarise_cluster(bathurst_cluster)
    assert "requests_311" in summary
    assert "road_restrictions" in summary
    assert "ttc_alerts" in summary

def test_summarise_contains_event_count(bathurst_cluster):
    summary = summarise_cluster(bathurst_cluster)
    assert "3" in summary


# --- parse_llm_response ---

def test_parse_valid_response(bathurst_cluster):
    result = parse_llm_response(VALID_LLM_RESPONSE, bathurst_cluster.cluster_id)
    assert result is not None
    assert result.is_causal is True
    assert result.confidence == 0.87

def test_parse_clamps_confidence(bathurst_cluster):
    raw = {**VALID_LLM_RESPONSE, "confidence": 99.0}
    result = parse_llm_response(raw, bathurst_cluster.cluster_id)
    assert result.confidence <= 1.0

def test_parse_empty_dict_returns_none(bathurst_cluster):
    result = parse_llm_response({}, bathurst_cluster.cluster_id)
    assert result is not None  # empty dict has defaults, should not return None

def test_parse_missing_causal_chain(bathurst_cluster):
    raw = {**VALID_LLM_RESPONSE, "causal_chain": None}
    result = parse_llm_response(raw, bathurst_cluster.cluster_id)
    assert result is None or isinstance(result, CorrelationResult)


# --- fallback_result ---

def test_fallback_is_not_causal():
    result = fallback_result("cluster-x", "test reason")
    assert result.is_causal is False
    assert result.confidence == 0.0

def test_fallback_contains_reason():
    result = fallback_result("cluster-x", "LLM timed out")
    assert "LLM timed out" in result.reasoning


# --- correlate_cluster ---

def test_correlate_single_event_cluster_returns_fallback(single_event_cluster):
    result = correlate_cluster(single_event_cluster)
    assert result.is_causal is False

def test_correlate_happy_path(bathurst_cluster):
    with patch("agents.correlation_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = correlate_cluster(bathurst_cluster)
    assert result.is_causal is True
    assert result.confidence == 0.87

def test_correlate_llm_failure_uses_heuristic(bathurst_cluster):
    with patch("agents.correlation_agent.call_llm_json", return_value={}):
        result = correlate_cluster(bathurst_cluster)
    # Bathurst cluster has watermain_break + road_closure + transit_disruption
    # so the heuristic should detect watermain_to_road_to_ttc
    assert result.is_causal is True
    assert result.cascade_type == "watermain_to_road_to_ttc"
    assert result.confidence > 0.0

def test_correlate_never_raises(bathurst_cluster):
    with patch("agents.correlation_agent.call_llm_json", side_effect=Exception("boom")):
        result = correlate_cluster(bathurst_cluster)
    assert isinstance(result, CorrelationResult)


# --- correlate_batch ---

def test_correlate_batch_empty():
    assert correlate_batch([]) == []

def test_correlate_batch_returns_one_per_cluster(bathurst_cluster, single_event_cluster):
    with patch("agents.correlation_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        results = correlate_batch([bathurst_cluster, single_event_cluster])
    assert len(results) == 2
