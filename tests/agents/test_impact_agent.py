import pytest
from unittest.mock import patch
from agents.impact_agent import (
    extract_affected_routes,
    base_severity,
    estimate_commuters,
    fallback_assessment,
    assess_impact,
    assess_batch,
)
from specs.data_contracts import ImpactAssessment, CorrelationResult
from config import MODEL

VALID_LLM_RESPONSE = {
    "estimated_duration_hours": 3.5,
    "recommended_actions": ["Close Bathurst", "Divert 511", "Notify Water Services"],
}


# --- extract_affected_routes ---

def test_extract_routes_bathurst(bathurst_cluster):
    routes = extract_affected_routes(bathurst_cluster)
    assert "511" in routes

def test_extract_routes_no_match(single_event_cluster):
    # single_event_cluster address is generic
    routes = extract_affected_routes(single_event_cluster)
    assert isinstance(routes, list)

def test_extract_routes_deduplicates(bathurst_cluster):
    routes = extract_affected_routes(bathurst_cluster)
    assert len(routes) == len(set(routes))


# --- base_severity ---

def test_base_severity_causal_high(bathurst_cluster, causal_correlation):
    score, breakdown = base_severity(bathurst_cluster, causal_correlation)
    assert 0 <= score <= 10
    assert "total" in breakdown

def test_base_severity_non_causal_lower(bathurst_cluster, causal_correlation):
    from specs.data_contracts import CorrelationResult
    non_causal = CorrelationResult(
        cluster_id=causal_correlation.cluster_id,
        is_causal=False,
        confidence=0.0,
        cascade_type="unrelated",
        causal_chain=[],
        reasoning="not causal",
        llm_model=MODEL,
    )
    causal_score, _ = base_severity(bathurst_cluster, causal_correlation)
    non_causal_score, _ = base_severity(bathurst_cluster, non_causal)
    assert causal_score >= non_causal_score

def test_base_severity_clamped(bathurst_cluster, causal_correlation):
    score, _ = base_severity(bathurst_cluster, causal_correlation)
    assert score <= 10


def test_estimate_commuters_with_511():
    assert estimate_commuters(["511"]) > 0

def test_estimate_commuters_empty():
    assert estimate_commuters([]) == 0


# --- fallback_assessment ---

def test_fallback_returns_impact_assessment():
    result = fallback_assessment("cluster-x", 5, ["511"])
    assert isinstance(result, ImpactAssessment)

def test_fallback_severity_preserved():
    result = fallback_assessment("cluster-x", 7, [])
    assert result.severity_score == 7

def test_fallback_has_actions():
    result = fallback_assessment("cluster-x", 3, [])
    assert len(result.recommended_actions) > 0


# --- assess_impact ---

def test_assess_impact_non_causal_returns_fallback(bathurst_cluster, causal_correlation):
    non_causal = causal_correlation.model_copy(update={"is_causal": False, "confidence": 0.0})
    result = assess_impact(bathurst_cluster, non_causal)
    assert isinstance(result, ImpactAssessment)

def test_assess_impact_happy_path(bathurst_cluster, causal_correlation):
    with patch("agents.impact_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = assess_impact(bathurst_cluster, causal_correlation)
    assert result.estimated_duration_hours == 3.5
    assert "Close Bathurst" in result.recommended_actions

def test_assess_impact_llm_failure_returns_fallback(bathurst_cluster, causal_correlation):
    with patch("agents.impact_agent.call_llm_json", return_value={}):
        result = assess_impact(bathurst_cluster, causal_correlation)
    assert isinstance(result, ImpactAssessment)

def test_assess_impact_llm_raises_returns_fallback(bathurst_cluster, causal_correlation):
    with patch("agents.impact_agent.call_llm_json", side_effect=Exception("boom")):
        result = assess_impact(bathurst_cluster, causal_correlation)
    assert isinstance(result, ImpactAssessment)

def test_assess_impact_negative_duration_clamped(bathurst_cluster, causal_correlation):
    with patch("agents.impact_agent.call_llm_json", return_value={**VALID_LLM_RESPONSE, "estimated_duration_hours": -1.0}):
        result = assess_impact(bathurst_cluster, causal_correlation)
    assert result.estimated_duration_hours >= 0.0


# --- assess_batch ---

def test_assess_batch_empty():
    assert assess_batch([], []) == []

def test_assess_batch_skips_missing_correlation(bathurst_cluster):
    results = assess_batch([bathurst_cluster], [])
    assert results == []

def test_assess_batch_returns_one_per_matched_cluster(bathurst_cluster, causal_correlation):
    with patch("agents.impact_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        results = assess_batch([bathurst_cluster], [causal_correlation])
    assert len(results) == 1
