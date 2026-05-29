import pytest
from unittest.mock import patch, MagicMock
from state.graph import run_pipeline, ingest_node, cluster_node, correlate_node, impact_node, brief_node, prediction_node
from state.schema import PipelineState
from specs.data_contracts import PredictedCascade, DispatchRecommendation
from datetime import datetime, timezone

MOCK_CORRELATION = {
    "is_causal": True,
    "confidence": 0.87,
    "cascade_type": "watermain_to_road_to_ttc",
    "causal_chain": ["Watermain break", "Road closure", "TTC diversion"],
    "reasoning": "Three events on same block within 32 minutes.",
}

MOCK_IMPACT = {
    "estimated_duration_hours": 3.5,
    "recommended_actions": ["Close Bathurst", "Divert 511"],
}

MOCK_BRIEF = {
    "headline": "Watermain break on Bathurst disrupts transit",
    "body": "Three related events detected on Bathurst St.",
    "recommended_actions": ["Deploy crew", "Divert 511"],
}


def fresh_state():
    return PipelineState(
        run_id="test-run",
        started_at=datetime.now(timezone.utc),
    )


# --- PipelineState ---

def test_pipeline_state_defaults():
    state = fresh_state()
    assert state.raw_events == []
    assert state.iteration_count == 0
    assert state.is_stuck() is False


def test_pipeline_state_with_error():
    state = fresh_state().with_error("something broke")
    assert "something broke" in state.errors


def test_pipeline_state_is_stuck():
    state = fresh_state()
    for _ in range(10):
        state = state.with_error("x")
    assert state.is_stuck() is True


def test_pipeline_state_immutable(bathurst_watermain_event):
    state = fresh_state()
    new_state = state.with_events([bathurst_watermain_event])
    assert len(state.raw_events) == 0
    assert len(new_state.raw_events) == 1


# --- ingest_node ---

def test_ingest_node_calls_all_feeds(bathurst_watermain_event):
    feed1 = lambda: [bathurst_watermain_event]
    feed2 = lambda: []
    state = ingest_node(fresh_state(), [feed1, feed2])
    assert len(state.raw_events) == 1


def test_ingest_node_handles_feed_exception():
    def bad_feed():
        raise Exception("network down")
    state = ingest_node(fresh_state(), [bad_feed])
    assert len(state.errors) > 0
    assert state.raw_events == []


def test_ingest_node_circuit_breaker():
    state = fresh_state()
    state = state.model_copy(update={"iteration_count": 10})
    result = ingest_node(state, [])
    assert any("Circuit breaker" in e for e in result.errors)


# --- cluster_node ---

def test_cluster_node_empty_events():
    state = fresh_state()
    result = cluster_node(state)
    assert result.clusters == []


def test_cluster_node_forms_cluster(bathurst_cluster):
    state = fresh_state().with_events(bathurst_cluster.events)
    result = cluster_node(state)
    assert len(result.clusters) >= 1


# --- correlate_node ---

def test_correlate_node_empty_clusters():
    state = fresh_state()
    result = correlate_node(state)
    assert result.correlations == []


def test_correlate_node_with_cluster(bathurst_cluster):
    state = fresh_state().with_clusters([bathurst_cluster])
    with patch("state.graph.correlate_batch") as mock:
        mock.return_value = []
        result = correlate_node(state)
    mock.assert_called_once()


# --- impact_node ---

def test_impact_node_empty_correlations():
    state = fresh_state()
    result = impact_node(state)
    assert result.impacts == []


# --- brief_node ---

def test_brief_node_empty_impacts():
    state = fresh_state()
    result = brief_node(state)
    assert result.briefs == []


# --- run_pipeline ---

def test_run_pipeline_returns_state():
    result = run_pipeline([])
    assert isinstance(result, PipelineState)
    assert result.run_id.startswith("run-")


def test_run_pipeline_no_feeds_produces_no_briefs():
    result = run_pipeline([])
    assert result.briefs == []


def test_run_pipeline_full_bathurst(bathurst_cluster):
    feed = lambda: bathurst_cluster.events
    with patch("state.graph.correlate_batch") as mock_corr, \
         patch("state.graph.assess_batch") as mock_impact, \
         patch("state.graph.generate_batch") as mock_brief, \
         patch("state.graph.predict_batch", return_value=[]), \
         patch("state.graph.save_dispatch"):
        mock_corr.return_value = []
        mock_impact.return_value = []
        mock_brief.return_value = []
        result = run_pipeline([feed])
    assert len(result.raw_events) == 3
    assert result.errors == []


# --- prediction_node ---

def _make_prediction(trigger_event_id: str = "311-test-001") -> PredictedCascade:
    return PredictedCascade(
        trigger_event_id=trigger_event_id,
        predicted_impacts=["Road closure likely", "511 at risk"],
        recommended_dispatches=[
            DispatchRecommendation(
                dispatch_id="dr-001",
                dispatch_type="water_repair",
                target_department="Toronto Water",
                message="Dispatch crew",
                priority="HIGH",
            )
        ],
        confidence=0.82,
        reasoning="Test prediction",
    )


def test_prediction_node_empty_events():
    state = fresh_state()
    with patch("state.graph.predict_batch", return_value=[]) as mock_pred, \
         patch("state.graph.save_dispatch"):
        result = prediction_node(state)
    assert result.predicted_cascades == []
    assert result.last_node == "predict"


def test_prediction_node_with_watermain_event(bathurst_watermain_event):
    pred = _make_prediction(bathurst_watermain_event.event_id)
    state = fresh_state().with_events([bathurst_watermain_event])
    with patch("state.graph.predict_batch", return_value=[pred]), \
         patch("state.graph.save_dispatch"):
        result = prediction_node(state)
    assert len(result.predicted_cascades) == 1
    assert result.predicted_cascades[0].trigger_event_id == bathurst_watermain_event.event_id


def test_prediction_node_saves_dispatches(bathurst_watermain_event):
    pred = _make_prediction(bathurst_watermain_event.event_id)
    state = fresh_state().with_events([bathurst_watermain_event])
    with patch("state.graph.predict_batch", return_value=[pred]), \
         patch("state.graph.save_dispatch") as mock_save:
        prediction_node(state)
    assert mock_save.call_count == 1


def test_prediction_node_skips_non_triggering_events(
    bathurst_road_closure_event, bathurst_ttc_alert_event
):
    state = fresh_state().with_events([bathurst_road_closure_event, bathurst_ttc_alert_event])
    with patch("state.graph.predict_batch", return_value=[]) as mock_pred, \
         patch("state.graph.save_dispatch"):
        result = prediction_node(state)
    assert result.predicted_cascades == []


def test_prediction_node_circuit_breaker():
    state = fresh_state().model_copy(update={"iteration_count": 10})
    result = prediction_node(state)
    assert any("Circuit breaker" in e for e in result.errors)


def test_prediction_node_preserves_raw_events(bathurst_watermain_event):
    state = fresh_state().with_events([bathurst_watermain_event])
    with patch("state.graph.predict_batch", return_value=[]), \
         patch("state.graph.save_dispatch"):
        result = prediction_node(state)
    assert len(result.raw_events) == 1


def test_run_pipeline_includes_predictions(bathurst_cluster):
    pred = _make_prediction(bathurst_cluster.events[0].event_id)
    feed = lambda: bathurst_cluster.events
    with patch("state.graph.predict_batch", return_value=[pred]), \
         patch("state.graph.save_dispatch"), \
         patch("state.graph.correlate_batch", return_value=[]), \
         patch("state.graph.assess_batch", return_value=[]), \
         patch("state.graph.generate_batch", return_value=[]):
        result = run_pipeline([feed])
    assert len(result.predicted_cascades) == 1


def test_run_pipeline_predictions_default_empty_on_no_triggers():
    """No watermain events → no predictions in final state."""
    feed = lambda: []
    with patch("state.graph.predict_batch", return_value=[]), \
         patch("state.graph.save_dispatch"):
        result = run_pipeline([feed])
    assert result.predicted_cascades == []
