import pytest
from datetime import datetime, timezone
from pydantic import ValidationError
from state.schema import PipelineState
from specs.data_contracts import (
    PredictedCascade, DispatchRecommendation,
    UnifiedEvent, SourceFeed, EventType,
)


def make_event(**overrides):
    base = dict(
        event_id="test-001",
        source=SourceFeed.REQUESTS_311,
        event_type=EventType.WATERMAIN_BREAK,
        latitude=43.7115,
        longitude=-79.4317,
        address="Bathurst St & Prue Ave",
        description="Watermain-Possible Break",
        severity_raw=2,
        timestamp=datetime(2024, 10, 2, 8, 43, tzinfo=timezone.utc),
        source_id="src-001",
    )
    base.update(overrides)
    return UnifiedEvent(**base)


def make_prediction(**overrides):
    base = dict(
        trigger_event_id="311-2024-bathurst-001",
        predicted_impacts=["Road closure on Bathurst", "511 disruption"],
        recommended_dispatches=[
            DispatchRecommendation(
                dispatch_id="dr-001",
                dispatch_type="ttc_diversion",
                target_department="TTC Operations",
                message="Pre-emptively notify 511 for possible diversion",
                priority="HIGH",
            )
        ],
        confidence=0.82,
        reasoning="Watermain on Bathurst historically cascades to road closure + TTC",
    )
    base.update(overrides)
    return PredictedCascade(**base)


def make_state(**overrides):
    base = dict(run_id="run-001", started_at=datetime(2024, 10, 2, tzinfo=timezone.utc))
    base.update(overrides)
    return PipelineState(**base)


# --- defaults ---

def test_predicted_cascades_defaults_empty():
    ps = make_state()
    assert ps.predicted_cascades == []


# --- with_predictions ---

def test_with_predictions_stores_predictions():
    ps = make_state()
    pred = make_prediction()
    ps2 = ps.with_predictions([pred])
    assert len(ps2.predicted_cascades) == 1
    assert ps2.predicted_cascades[0].trigger_event_id == "311-2024-bathurst-001"


def test_with_predictions_sets_last_node():
    ps = make_state()
    ps2 = ps.with_predictions([make_prediction()])
    assert ps2.last_node == "predict"


def test_with_predictions_increments_iteration():
    ps = make_state()
    ps2 = ps.with_predictions([make_prediction()])
    assert ps2.iteration_count == 1


def test_with_predictions_empty_list_allowed():
    ps = make_state()
    ps2 = ps.with_predictions([])
    assert ps2.predicted_cascades == []
    assert ps2.last_node == "predict"


def test_with_predictions_multiple():
    ps = make_state()
    preds = [
        make_prediction(trigger_event_id=f"311-{i}", confidence=0.5 + i * 0.1)
        for i in range(3)
    ]
    ps2 = ps.with_predictions(preds)
    assert len(ps2.predicted_cascades) == 3


def test_with_predictions_does_not_mutate_original():
    ps = make_state()
    ps.with_predictions([make_prediction()])
    assert ps.predicted_cascades == []
    assert ps.last_node == ""


# --- with_predictions preserves other state ---

def test_with_predictions_preserves_events():
    event = make_event()
    ps = make_state().with_events([event])
    ps2 = ps.with_predictions([make_prediction()])
    assert len(ps2.raw_events) == 1
    assert ps2.raw_events[0].event_id == "test-001"


def test_with_predictions_preserves_errors():
    ps = make_state().with_error("something failed")
    ps2 = ps.with_predictions([make_prediction()])
    assert ps2.errors == ["something failed"]


# --- existing with_* methods still work after schema change ---

def test_existing_with_events_unaffected():
    ps = make_state()
    ps2 = ps.with_events([make_event()])
    assert ps2.last_node == "ingest"
    assert ps2.predicted_cascades == []


def test_is_stuck_unaffected():
    ps = PipelineState(
        run_id="r",
        started_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        iteration_count=10,
    )
    assert ps.is_stuck() is True
