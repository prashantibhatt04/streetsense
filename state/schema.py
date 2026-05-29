from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field
from specs.data_contracts import (
    UnifiedEvent, ClusterCandidate, CorrelationResult,
    ImpactAssessment, OperationalBrief, DispatchPayload, PredictedCascade,
)


class PipelineState(BaseModel):
    run_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_events: list[UnifiedEvent] = Field(default_factory=list)
    clusters: list[ClusterCandidate] = Field(default_factory=list)
    correlations: list[CorrelationResult] = Field(default_factory=list)
    impacts: list[ImpactAssessment] = Field(default_factory=list)
    briefs: list[OperationalBrief] = Field(default_factory=list)
    dispatch_payloads: list[DispatchPayload] = Field(default_factory=list)
    predicted_cascades: list[PredictedCascade] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    iteration_count: int = Field(default=0, ge=0)
    human_approved: bool = False
    last_node: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)

    def with_events(self, events: list[UnifiedEvent]) -> "PipelineState":
        return self.model_copy(update={
            "raw_events": events,
            "last_node": "ingest",
            "iteration_count": self.iteration_count + 1,
        })

    def with_clusters(self, clusters: list[ClusterCandidate]) -> "PipelineState":
        return self.model_copy(update={
            "clusters": clusters,
            "last_node": "cluster",
            "iteration_count": self.iteration_count + 1,
        })

    def with_correlations(self, correlations: list[CorrelationResult]) -> "PipelineState":
        return self.model_copy(update={
            "correlations": correlations,
            "last_node": "correlate",
            "iteration_count": self.iteration_count + 1,
        })

    def with_impacts(self, impacts: list[ImpactAssessment]) -> "PipelineState":
        return self.model_copy(update={
            "impacts": impacts,
            "last_node": "impact",
            "iteration_count": self.iteration_count + 1,
        })

    def with_briefs(self, briefs: list[OperationalBrief]) -> "PipelineState":
        return self.model_copy(update={
            "briefs": briefs,
            "last_node": "brief",
            "iteration_count": self.iteration_count + 1,
        })

    def with_predictions(self, predictions: list[PredictedCascade]) -> "PipelineState":
        return self.model_copy(update={
            "predicted_cascades": predictions,
            "last_node": "predict",
            "iteration_count": self.iteration_count + 1,
        })

    def with_error(self, error: str) -> "PipelineState":
        return self.model_copy(update={
            "errors": self.errors + [error],
            "iteration_count": self.iteration_count + 1,
        })

    def is_stuck(self) -> bool:
        """Circuit breaker — True if iteration ceiling exceeded."""
        return self.iteration_count >= 10
