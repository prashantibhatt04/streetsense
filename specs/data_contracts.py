from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class SourceFeed(str, Enum):
    ROAD_RESTRICTIONS = "road_restrictions"
    TTC_ALERTS = "ttc_alerts"
    UTILITY_CUTS = "utility_cuts"
    REQUESTS_311 = "requests_311"


class EventType(str, Enum):
    WATERMAIN_BREAK = "watermain_break"
    ROAD_CLOSURE = "road_closure"
    TRANSIT_DISRUPTION = "transit_disruption"
    UTILITY_WORK = "utility_work"
    FLOODING = "flooding"
    SEWER_BACKUP = "sewer_backup"
    UNKNOWN = "unknown"


# Real-world 311 call types that don't appear in Toronto open data feeds but arrive
# in mock/imported data. Mapped to the closest canonical EventType.
_EVENT_TYPE_ALIASES: dict[str, str] = {
    "road_flooding":        "flooding",
    "catch_basin_flooding": "flooding",
    "street_flooding":      "flooding",
    "storm_flooding":       "flooding",
    "basement_flooding":    "sewer_backup",
    "manhole_hazard":       "flooding",
    "water_main_break":     "watermain_break",
    "watermain":            "watermain_break",
}


class UnifiedEvent(BaseModel):
    event_id: str
    source: SourceFeed
    event_type: EventType
    latitude: float
    longitude: float
    address: str
    description: str
    severity_raw: int = Field(ge=0, le=5)
    timestamp: datetime
    source_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type", mode="before")
    @classmethod
    def coerce_event_type(cls, v: object) -> object:
        if isinstance(v, str):
            return _EVENT_TYPE_ALIASES.get(v, v)
        return v

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, v: float) -> float:
        if not (43.58 <= v <= 43.86):
            raise ValueError(f"Latitude {v} outside Toronto bounds")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, v: float) -> float:
        if not (-79.64 <= v <= -79.11):
            raise ValueError(f"Longitude {v} outside Toronto bounds")
        return v


class ClusterCandidate(BaseModel):
    cluster_id: str
    events: list[UnifiedEvent] = Field(min_length=1)
    centroid_lat: float
    centroid_lng: float
    radius_metres: float = Field(ge=0)
    time_window_minutes: int = Field(ge=0)


CascadeType = Literal[
    "watermain_to_road",
    "road_to_ttc",
    "watermain_to_road_to_ttc",
    "utility_to_road",
    "flooding_cascade",
    "unrelated",
]


class CorrelationResult(BaseModel):
    cluster_id: str
    is_causal: bool
    confidence: float = Field(ge=0.0, le=1.0)
    cascade_type: CascadeType = "unrelated"
    causal_chain: list[str]
    reasoning: str
    llm_model: str
    at_risk_routes: list[str] = Field(default_factory=list)  # F6: routes near cluster with no alert yet


class ResidentImpactScore(BaseModel):
    score: int = Field(ge=0, le=10)
    commuters_affected: int = Field(ge=0)
    nearby_hospitals: list[str] = Field(default_factory=list)   # names only
    nearby_schools: list[str] = Field(default_factory=list)      # names only
    neighbourhood_population: int = Field(ge=0)
    is_peak_hours: bool
    factors: list[str] = Field(default_factory=list)  # human-readable scoring reasons


class ImpactAssessment(BaseModel):
    cluster_id: str
    severity_score: int = Field(ge=0, le=10)
    affected_routes: list[str] = Field(default_factory=list)
    estimated_commuters: int = Field(default=0, ge=0)
    estimated_duration_hours: float = Field(ge=0)
    recommended_actions: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    resident_impact: Optional[ResidentImpactScore] = None


class HistoricalMatch(BaseModel):
    """Pattern from the pattern_memory table — populated by MemoryAgent overnight."""
    match_found: bool
    similar_date: Optional[str] = None
    corridor: Optional[str] = None
    outcome: Optional[str] = None
    uncoordinated_hours: Optional[float] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperationalBrief(BaseModel):
    brief_id: str
    generated_at: datetime
    cluster_id: str
    headline: str
    body: str
    severity_score: int = Field(ge=0, le=10)
    recommended_actions: list[str]
    source_event_count: int = Field(ge=0)
    historical_match: Optional[HistoricalMatch] = None
    estimated_commuters: int = Field(default=0, ge=0)
    affected_routes: list[str] = Field(default_factory=list)
    at_risk_routes: list[str] = Field(default_factory=list)  # F6
    resident_impact: Optional[ResidentImpactScore] = None


class DispatchRecommendation(BaseModel):
    """A proactive dispatch recommendation from the prediction agent, awaiting supervisor approval."""
    dispatch_id: str
    dispatch_type: Literal["water_repair", "ttc_diversion", "road_closure", "notify_department"]
    target_department: str
    message: str
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    status: Literal["AWAITING_APPROVAL", "APPROVED", "REJECTED"] = "AWAITING_APPROVAL"
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: Optional[datetime] = None


class PredictedCascade(BaseModel):
    """Forward-looking cascade prediction triggered by a single early 311 event."""
    trigger_event_id: str
    predicted_impacts: list[str] = Field(default_factory=list)
    recommended_dispatches: list[DispatchRecommendation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class DispatchPayload(BaseModel):
    """Structured action contract. Any city system consumes this JSON."""
    action_type: Literal[
        "notify_department",
        "emergency_flood_response",
        "suggest_ttc_short_turn",
        "surface_bike_share",
        "surface_parking",
    ]
    priority: Literal["low", "medium", "high", "critical"]
    target_department: str
    payload: dict[str, Any]
    requires_human_approval: bool = True


class PublicCommunicationDraft(BaseModel):
    cluster_id: str
    generated_at: datetime
    ttc_alert: str          # TTC service alert format, under 280 chars
    councillor_email: str   # Ward councillor notification email body
    social_post: str        # Twitter/X length, under 280 chars, no hashtags
    approved_by_supervisor: bool = False
    generated_for_severity: int = Field(ge=0, le=10)


class WriteResult(BaseModel):
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)


class PipelineState(BaseModel):
    run_id: str
    started_at: datetime
    raw_events: list[UnifiedEvent] = Field(default_factory=list)
    clusters: list[ClusterCandidate] = Field(default_factory=list)
    correlations: list[CorrelationResult] = Field(default_factory=list)
    impacts: list[ImpactAssessment] = Field(default_factory=list)
    briefs: list[OperationalBrief] = Field(default_factory=list)
    dispatch_payloads: list[DispatchPayload] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    iteration_count: int = Field(default=0, ge=0)
    human_approved: bool = False
    last_node: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)

    def with_events(self, events: list[UnifiedEvent]) -> "PipelineState":
        return self.model_copy(update={"raw_events": events, "last_node": "ingest",
                                       "iteration_count": self.iteration_count + 1})

    def with_clusters(self, clusters: list[ClusterCandidate]) -> "PipelineState":
        return self.model_copy(update={"clusters": clusters, "last_node": "cluster",
                                       "iteration_count": self.iteration_count + 1})

    def with_correlations(self, correlations: list[CorrelationResult]) -> "PipelineState":
        return self.model_copy(update={"correlations": correlations, "last_node": "correlate",
                                       "iteration_count": self.iteration_count + 1})

    def with_impacts(self, impacts: list[ImpactAssessment]) -> "PipelineState":
        return self.model_copy(update={"impacts": impacts, "last_node": "impact",
                                       "iteration_count": self.iteration_count + 1})

    def with_briefs(self, briefs: list[OperationalBrief]) -> "PipelineState":
        return self.model_copy(update={"briefs": briefs, "last_node": "brief",
                                       "iteration_count": self.iteration_count + 1})

    def with_error(self, error: str) -> "PipelineState":
        return self.model_copy(update={"errors": self.errors + [error],
                                       "iteration_count": self.iteration_count + 1})

    def is_stuck(self) -> bool:
        return self.iteration_count >= 10
