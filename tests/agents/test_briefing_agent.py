import pytest
from unittest.mock import patch
from agents.briefing_agent import (
    summarise_correlation,
    summarise_impact,
    fallback_brief,
    generate_brief,
    generate_batch,
    build_dispatch,
)
from specs.data_contracts import OperationalBrief, ImpactAssessment
from config import MODEL

VALID_LLM_RESPONSE = {
    "headline": "Watermain break on Bathurst disrupts transit and road access",
    "body": "Three related events detected on Bathurst St within 32 minutes. A watermain break triggered an emergency road closure and 511 streetcar diversion. Water Services and TTC are responding.",
    "recommended_actions": ["Deploy water repair crew", "Activate 511 diversion", "Update 511 riders"],
}


@pytest.fixture
def sample_impact(bathurst_cluster, causal_correlation):
    return ImpactAssessment(
        cluster_id=bathurst_cluster.cluster_id,
        severity_score=8,
        affected_routes=["511"],
        estimated_duration_hours=3.5,
        recommended_actions=["Close Bathurst", "Divert 511"],
    )


# --- summarise_correlation ---

def test_summarise_correlation_contains_confidence(causal_correlation):
    summary = summarise_correlation(causal_correlation)
    assert "87%" in summary

def test_summarise_correlation_contains_chain(causal_correlation):
    summary = summarise_correlation(causal_correlation)
    assert "Watermain break" in summary

def test_summarise_correlation_causal_flag(causal_correlation):
    summary = summarise_correlation(causal_correlation)
    assert "True" in summary


# --- summarise_impact ---

def test_summarise_impact_contains_severity(sample_impact):
    summary = summarise_impact(sample_impact)
    assert "8/10" in summary

def test_summarise_impact_contains_routes(sample_impact):
    summary = summarise_impact(sample_impact)
    assert "511" in summary

def test_summarise_impact_contains_duration(sample_impact):
    summary = summarise_impact(sample_impact)
    assert "3.5" in summary


# --- fallback_brief ---

def test_fallback_brief_returns_operational_brief(bathurst_cluster, sample_impact):
    result = fallback_brief(bathurst_cluster, sample_impact)
    assert isinstance(result, OperationalBrief)

def test_fallback_brief_event_count(bathurst_cluster, sample_impact):
    result = fallback_brief(bathurst_cluster, sample_impact)
    assert result.source_event_count == 3

def test_fallback_brief_severity(bathurst_cluster, sample_impact):
    result = fallback_brief(bathurst_cluster, sample_impact)
    assert result.severity_score == 8

def test_fallback_brief_has_headline(bathurst_cluster, sample_impact):
    result = fallback_brief(bathurst_cluster, sample_impact)
    assert len(result.headline) > 0


# --- generate_brief ---

def test_generate_brief_happy_path(bathurst_cluster, causal_correlation, sample_impact):
    with patch("agents.briefing_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_brief(bathurst_cluster, causal_correlation, sample_impact)
    assert result.headline == VALID_LLM_RESPONSE["headline"]
    assert result.source_event_count == 3

def test_generate_brief_llm_failure_returns_fallback(bathurst_cluster, causal_correlation, sample_impact):
    with patch("agents.briefing_agent.call_llm_json", return_value={}):
        result = generate_brief(bathurst_cluster, causal_correlation, sample_impact)
    assert isinstance(result, OperationalBrief)

def test_generate_brief_llm_raises_returns_fallback(bathurst_cluster, causal_correlation, sample_impact):
    with patch("agents.briefing_agent.call_llm_json", side_effect=Exception("boom")):
        result = generate_brief(bathurst_cluster, causal_correlation, sample_impact)
    assert isinstance(result, OperationalBrief)

def test_generate_brief_cluster_id_preserved(bathurst_cluster, causal_correlation, sample_impact):
    with patch("agents.briefing_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_brief(bathurst_cluster, causal_correlation, sample_impact)
    assert result.cluster_id == bathurst_cluster.cluster_id


# --- generate_batch ---

def test_generate_batch_empty():
    assert generate_batch([], [], []) == []

def test_generate_batch_skips_missing_impact(bathurst_cluster, causal_correlation):
    results = generate_batch([bathurst_cluster], [causal_correlation], [])
    assert results == []

def test_generate_batch_skips_missing_correlation(bathurst_cluster, sample_impact):
    results = generate_batch([bathurst_cluster], [], [sample_impact])
    assert results == []

def test_generate_batch_returns_one_per_cluster(bathurst_cluster, causal_correlation, sample_impact):
    with patch("agents.briefing_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        results = generate_batch([bathurst_cluster], [causal_correlation], [sample_impact])
    assert len(results) == 1


# --- build_dispatch ---

def test_build_dispatch_flooding_cascade_action(bathurst_cluster, sample_impact):
    from specs.data_contracts import CorrelationResult
    flood_corr = CorrelationResult(
        cluster_id=bathurst_cluster.cluster_id,
        is_causal=True,
        confidence=0.90,
        cascade_type="flooding_cascade",
        causal_chain=["Flooding at DVP"],
        reasoning="Citywide flash flood",
        llm_model="gemma4:latest",
    )
    with patch("agents.briefing_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        brief = generate_brief(bathurst_cluster, flood_corr, sample_impact)
    payload = build_dispatch(brief, flood_corr)
    assert payload.action_type == "emergency_flood_response"
    assert "Emergency Management" in payload.target_department


def test_build_dispatch_watermain_cascade_action(bathurst_cluster, causal_correlation, sample_impact):
    with patch("agents.briefing_agent.call_llm_json", return_value=VALID_LLM_RESPONSE):
        brief = generate_brief(bathurst_cluster, causal_correlation, sample_impact)
    payload = build_dispatch(brief, causal_correlation)
    assert payload.action_type == "suggest_ttc_short_turn"
    assert "Toronto Water" in payload.target_department
