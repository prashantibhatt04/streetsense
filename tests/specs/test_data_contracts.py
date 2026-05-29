import pytest
from datetime import datetime, timezone
from pydantic import ValidationError
from specs.data_contracts import (
    UnifiedEvent, ClusterCandidate, CorrelationResult,
    ImpactAssessment, OperationalBrief, WriteResult, PipelineState,
    SourceFeed, EventType, DispatchRecommendation, PredictedCascade,
    _EVENT_TYPE_ALIASES,
)


def make_event(**overrides):
    base = dict(
        event_id="test-001",
        source=SourceFeed.REQUESTS_311,
        event_type=EventType.WATERMAIN_BREAK,
        latitude=43.7115,
        longitude=-79.4317,
        address="Bathurst & Prue",
        description="Test event",
        severity_raw=2,
        timestamp=datetime(2024, 10, 2, 8, 43, tzinfo=timezone.utc),
        source_id="src-001",
    )
    base.update(overrides)
    return base


# --- UnifiedEvent ---

def test_unified_event_happy_path():
    e = UnifiedEvent(**make_event())
    assert e.event_id == "test-001"
    assert e.source == SourceFeed.REQUESTS_311


def test_unified_event_latitude_out_of_bounds():
    with pytest.raises(ValidationError):
        UnifiedEvent(**make_event(latitude=43.10))  # well south of Toronto

def test_unified_event_longitude_out_of_bounds():
    with pytest.raises(ValidationError):
        UnifiedEvent(**make_event(longitude=-80.00))  # well west of Toronto

def test_unified_event_severity_out_of_range():
    with pytest.raises(ValidationError):
        UnifiedEvent(**make_event(severity_raw=6))


def test_unified_event_default_metadata():
    e = UnifiedEvent(**make_event())
    assert e.metadata == {}


def test_unified_event_fixtures_valid(bathurst_watermain_event, bathurst_road_closure_event, bathurst_ttc_alert_event):
    for event in [bathurst_watermain_event, bathurst_road_closure_event, bathurst_ttc_alert_event]:
        assert isinstance(event, UnifiedEvent)


# --- ClusterCandidate ---

def test_cluster_happy_path(bathurst_cluster):
    assert len(bathurst_cluster.events) == 3
    assert bathurst_cluster.radius_metres == 150.0


def test_cluster_single_event_allowed(single_event_cluster):
    assert len(single_event_cluster.events) == 1


def test_cluster_empty_events_rejected():
    with pytest.raises(ValidationError):
        ClusterCandidate(
            cluster_id="x", events=[],
            centroid_lat=43.71, centroid_lng=-79.43,
            radius_metres=0, time_window_minutes=0,
        )


# --- CorrelationResult ---

def test_correlation_happy_path(causal_correlation):
    assert causal_correlation.is_causal is True
    assert causal_correlation.confidence == 0.87


def test_correlation_has_cascade_type(causal_correlation):
    assert causal_correlation.cascade_type == "watermain_to_road_to_ttc"


def test_correlation_cascade_type_defaults_to_unrelated(bathurst_cluster):
    r = CorrelationResult(
        cluster_id=bathurst_cluster.cluster_id,
        is_causal=False, confidence=0.0,
        causal_chain=[], reasoning="x", llm_model="test",
    )
    assert r.cascade_type == "unrelated"


def test_correlation_confidence_out_of_range(bathurst_cluster):
    with pytest.raises(ValidationError):
        CorrelationResult(
            cluster_id=bathurst_cluster.cluster_id,
            is_causal=True, confidence=1.5,
            causal_chain=[], reasoning="x", llm_model="test",
        )


# --- ImpactAssessment ---

def test_impact_assessment_valid():
    ia = ImpactAssessment(
        cluster_id="c1", severity_score=7,
        affected_routes=["511"], estimated_duration_hours=2.5,
        recommended_actions=["Close Bathurst"],
    )
    assert ia.severity_score == 7


def test_impact_severity_out_of_range():
    with pytest.raises(ValidationError):
        ImpactAssessment(cluster_id="c1", severity_score=11,
                         estimated_duration_hours=1)


# --- WriteResult ---

def test_write_result_defaults():
    wr = WriteResult(success_count=5, failure_count=0)
    assert wr.errors == []


# --- PipelineState ---

def test_pipeline_state_defaults():
    ps = PipelineState(
        run_id="run-001",
        started_at=datetime(2024, 10, 2, tzinfo=timezone.utc),
    )
    assert ps.raw_events == []
    assert ps.iteration_count == 0


# --- DispatchRecommendation ---

def test_dispatch_recommendation_happy_path():
    dr = DispatchRecommendation(
        dispatch_id="dr-001",
        dispatch_type="ttc_diversion",
        target_department="TTC Operations",
        message="Divert 511 via Davenport due to watermain break",
        priority="HIGH",
    )
    assert dr.dispatch_id == "dr-001"
    assert dr.status == "AWAITING_APPROVAL"


def test_dispatch_recommendation_default_status():
    dr = DispatchRecommendation(
        dispatch_id="dr-002",
        dispatch_type="water_repair",
        target_department="Toronto Water",
        message="Dispatch repair crew to Bathurst & Prue",
        priority="HIGH",
    )
    assert dr.status == "AWAITING_APPROVAL"


def test_dispatch_recommendation_explicit_approved_status():
    dr = DispatchRecommendation(
        dispatch_id="dr-003",
        dispatch_type="road_closure",
        target_department="Transportation Services",
        message="Close Bathurst northbound at Prue Ave",
        priority="MEDIUM",
        status="APPROVED",
    )
    assert dr.status == "APPROVED"


def test_dispatch_recommendation_explicit_rejected_status():
    dr = DispatchRecommendation(
        dispatch_id="dr-004",
        dispatch_type="notify_department",
        target_department="Toronto Water",
        message="Heads up on possible break",
        priority="LOW",
        status="REJECTED",
    )
    assert dr.status == "REJECTED"


def test_dispatch_recommendation_invalid_dispatch_type():
    with pytest.raises(ValidationError):
        DispatchRecommendation(
            dispatch_id="dr-005",
            dispatch_type="unknown_type",
            target_department="TTC",
            message="test",
            priority="HIGH",
        )


def test_dispatch_recommendation_invalid_priority():
    with pytest.raises(ValidationError):
        DispatchRecommendation(
            dispatch_id="dr-006",
            dispatch_type="ttc_diversion",
            target_department="TTC",
            message="test",
            priority="URGENT",
        )


def test_dispatch_recommendation_invalid_status():
    with pytest.raises(ValidationError):
        DispatchRecommendation(
            dispatch_id="dr-007",
            dispatch_type="ttc_diversion",
            target_department="TTC",
            message="test",
            priority="HIGH",
            status="PENDING",
        )


# --- PredictedCascade ---

def test_predicted_cascade_happy_path():
    pc = PredictedCascade(
        trigger_event_id="311-2024-bathurst-001",
        predicted_impacts=["Road closure on Bathurst", "511 streetcar disruption"],
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
        reasoning="Watermain breaks on Bathurst historically cause road closures within 1-2 hours",
    )
    assert pc.trigger_event_id == "311-2024-bathurst-001"
    assert len(pc.predicted_impacts) == 2
    assert len(pc.recommended_dispatches) == 1
    assert pc.confidence == 0.82


def test_predicted_cascade_empty_dispatches_allowed():
    pc = PredictedCascade(
        trigger_event_id="311-xyz",
        confidence=0.3,
        reasoning="Low confidence, not enough signal",
    )
    assert pc.predicted_impacts == []
    assert pc.recommended_dispatches == []


def test_predicted_cascade_confidence_upper_bound():
    with pytest.raises(ValidationError):
        PredictedCascade(
            trigger_event_id="311-xyz",
            confidence=1.1,
            reasoning="over 100%",
        )


def test_predicted_cascade_confidence_lower_bound():
    with pytest.raises(ValidationError):
        PredictedCascade(
            trigger_event_id="311-xyz",
            confidence=-0.1,
            reasoning="negative",
        )


def test_predicted_cascade_multiple_dispatches():
    dispatches = [
        DispatchRecommendation(
            dispatch_id=f"dr-{i}",
            dispatch_type="ttc_diversion",
            target_department="TTC Operations",
            message=f"Dispatch {i}",
            priority="MEDIUM",
        )
        for i in range(3)
    ]
    pc = PredictedCascade(
        trigger_event_id="311-multi",
        predicted_impacts=["impact A", "impact B"],
        recommended_dispatches=dispatches,
        confidence=0.75,
        reasoning="multiple cascades expected",
    )
    assert len(pc.recommended_dispatches) == 3


# --- EventType aliasing ---

@pytest.mark.parametrize("alias,expected", [
    ("road_flooding",        EventType.FLOODING),
    ("catch_basin_flooding", EventType.FLOODING),
    ("street_flooding",      EventType.FLOODING),
    ("storm_flooding",       EventType.FLOODING),
    ("basement_flooding",    EventType.SEWER_BACKUP),
    ("manhole_hazard",       EventType.FLOODING),
    ("water_main_break",     EventType.WATERMAIN_BREAK),
])
def test_event_type_alias_coerced(alias, expected):
    e = UnifiedEvent(**make_event(event_type=alias))
    assert e.event_type == expected


def test_event_type_canonical_string_unchanged():
    e = UnifiedEvent(**make_event(event_type="flooding"))
    assert e.event_type == EventType.FLOODING


def test_event_type_unknown_alias_still_fails():
    with pytest.raises(Exception):
        UnifiedEvent(**make_event(event_type="teleportation_disruption"))


def test_all_aliases_map_to_valid_event_types():
    valid = {e.value for e in EventType}
    for alias, canonical in _EVENT_TYPE_ALIASES.items():
        assert canonical in valid, f"{alias!r} maps to {canonical!r} which is not a valid EventType"
