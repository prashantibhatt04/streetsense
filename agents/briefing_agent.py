import logging
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from specs.data_contracts import (
    ClusterCandidate, CorrelationResult, ImpactAssessment,
    OperationalBrief, HistoricalMatch, DispatchPayload,
)
from tools.llm_tools import call_llm_json, build_briefing_prompt
from tools.db_tools import lookup_historical_pattern
from config import MODEL
from state import agent_log

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5

TORONTO_TZ = ZoneInfo("America/Toronto")


def _fmt_local(ts: datetime) -> str:
    """Format a UTC datetime as Toronto local time for display in briefs."""
    local = ts.astimezone(TORONTO_TZ)
    return local.strftime("%H:%M")

# Cascade type → action type mapping for dispatch payload
_DISPATCH_ACTION = {
    "watermain_to_road_to_ttc": "suggest_ttc_short_turn",
    "watermain_to_road":        "notify_department",
    "road_to_ttc":              "suggest_ttc_short_turn",
    "flooding_cascade":         "emergency_flood_response",
    "utility_to_road":          "notify_department",
    "unrelated":                "notify_department",
}

_PRIORITY = {range(8, 11): "critical", range(6, 8): "high",
             range(4, 6): "medium", range(0, 4): "low"}

def _priority(score: int) -> str:
    if score >= 8: return "critical"
    if score >= 6: return "high"
    if score >= 4: return "medium"
    return "low"

def _extract_corridor(cluster: ClusterCandidate) -> str:
    """Best-guess corridor name from event addresses."""
    keywords = ["bathurst", "queen", "king", "dundas", "spadina",
                "college", "bloor", "yonge", "avenue", "st clair"]
    text = " ".join(e.address.lower() for e in cluster.events)
    for kw in keywords:
        if kw in text:
            return kw
    return "toronto"


def summarise_correlation(correlation: CorrelationResult) -> str:
    lines = [
        f"Causal: {correlation.is_causal}",
        f"Confidence: {correlation.confidence:.0%}",
        f"Cascade type: {correlation.cascade_type}",
        f"Reasoning: {correlation.reasoning}",
        "Causal chain:",
    ]
    for step in correlation.causal_chain:
        lines.append(f"  - {step}")
    return "\n".join(lines)


def summarise_impact(impact: ImpactAssessment) -> str:
    lines = [
        f"Severity: {impact.severity_score}/10",
        f"Estimated duration: {impact.estimated_duration_hours:.1f} hours",
        f"Estimated commuters affected: {impact.estimated_commuters:,}",
        f"Affected TTC routes: {', '.join(impact.affected_routes) or 'none identified'}",
        "Recommended actions:",
    ]
    for action in impact.recommended_actions:
        lines.append(f"  - {action}")
    return "\n".join(lines)


def fallback_brief(cluster: ClusterCandidate, impact: ImpactAssessment,
                   history: HistoricalMatch | None = None,
                   at_risk_routes: list[str] | None = None) -> OperationalBrief:
    return OperationalBrief(
        brief_id=f"brief-{uuid.uuid4().hex[:8]}",
        generated_at=datetime.now(timezone.utc),
        cluster_id=cluster.cluster_id,
        headline=f"Infrastructure incident detected — severity {impact.severity_score}/10",
        body=(
            f"A cluster of {len(cluster.events)} related infrastructure events has been detected. "
            f"Estimated duration: {impact.estimated_duration_hours:.1f} hours. "
            f"Affected TTC routes: {', '.join(impact.affected_routes) or 'none identified'}. "
            f"Est. {impact.estimated_commuters:,} commuters affected."
        ),
        severity_score=impact.severity_score,
        recommended_actions=impact.recommended_actions,
        source_event_count=len(cluster.events),
        historical_match=history,
        estimated_commuters=impact.estimated_commuters,
        affected_routes=impact.affected_routes,
        at_risk_routes=at_risk_routes or [],
        resident_impact=impact.resident_impact,
    )


def build_dispatch(brief: OperationalBrief,
                   correlation: CorrelationResult) -> DispatchPayload:
    """Build the structured dispatch payload. Requires human_approved=True in pipeline."""
    dept_map = {
        "watermain_to_road_to_ttc": "Toronto Water + TTC Operations",
        "watermain_to_road":        "Toronto Water",
        "road_to_ttc":              "TTC Operations",
        "flooding_cascade":         "Emergency Management + Toronto Water",
        "utility_to_road":          "Transportation Services",
        "unrelated":                "Transportation Services",
    }
    return DispatchPayload(
        action_type=_DISPATCH_ACTION.get(correlation.cascade_type, "notify_department"),
        priority=_priority(brief.severity_score),
        target_department=dept_map.get(correlation.cascade_type, "Transportation Services"),
        payload={
            "cluster_id": brief.cluster_id,
            "headline": brief.headline,
            "severity_score": brief.severity_score,
            "estimated_commuters": brief.estimated_commuters,
            "affected_routes": brief.affected_routes,
            "recommended_actions": brief.recommended_actions,
            "historical_match": (
                {
                    "similar_date": brief.historical_match.similar_date,
                    "uncoordinated_hours": brief.historical_match.uncoordinated_hours,
                    "outcome": brief.historical_match.outcome,
                }
                if brief.historical_match and brief.historical_match.match_found
                else None
            ),
        },
        requires_human_approval=True,
    )


def generate_brief(
    cluster: ClusterCandidate,
    correlation: CorrelationResult,
    impact: ImpactAssessment,
) -> OperationalBrief:
    corridor = _extract_corridor(cluster)
    history = lookup_historical_pattern(correlation.cascade_type, corridor)

    if history.match_found:
        agent_log.append(
            f"Historical match: {corridor.title()} corridor — "
            f"similar event {history.similar_date}, "
            f"uncoordinated {history.uncoordinated_hours:.0f}h"
        )

    correlation_summary = summarise_correlation(correlation)
    impact_summary = summarise_impact(impact)
    prompt = build_briefing_prompt(correlation_summary, impact_summary)

    agent_log.append(f"Generating operational brief (severity {impact.severity_score}/10)…")

    for attempt in range(MAX_ITERATIONS):
        try:
            raw = call_llm_json(prompt)
        except Exception as e:
            logger.warning("LLM call raised on attempt %d: %s", attempt + 1, e)
            continue
        if not raw:
            continue
        try:
            brief = OperationalBrief(
                brief_id=f"brief-{uuid.uuid4().hex[:8]}",
                generated_at=datetime.now(timezone.utc),
                cluster_id=cluster.cluster_id,
                headline=str(raw.get("headline", "")),
                body=str(raw.get("body", "")),
                severity_score=impact.severity_score,
                recommended_actions=list(raw.get("recommended_actions", [])),
                source_event_count=len(cluster.events),
                historical_match=history,
                estimated_commuters=impact.estimated_commuters,
                affected_routes=impact.affected_routes,
                at_risk_routes=correlation.at_risk_routes,
                resident_impact=impact.resident_impact,
            )
            agent_log.append(f"Brief generated: {brief.headline[:80]}")
            agent_log.append("Dispatch awaiting supervisor approval — click APPROVE in dashboard")
            return brief
        except Exception as e:
            logger.warning("Failed to parse brief response: %s", e)
            continue

    fb = fallback_brief(cluster, impact, history, correlation.at_risk_routes)
    return fb


def generate_batch(
    clusters: list[ClusterCandidate],
    correlations: list[CorrelationResult],
    impacts: list[ImpactAssessment],
) -> list[OperationalBrief]:
    correlation_map = {c.cluster_id: c for c in correlations}
    impact_map = {i.cluster_id: i for i in impacts}
    results = []
    for cluster in clusters:
        correlation = correlation_map.get(cluster.cluster_id)
        impact = impact_map.get(cluster.cluster_id)
        if not correlation or not impact:
            logger.warning("Missing correlation or impact for cluster %s", cluster.cluster_id)
            continue
        results.append(generate_brief(cluster, correlation, impact))
    return results


def build_dispatch_batch(
    briefs: list[OperationalBrief],
    correlations: list[CorrelationResult],
) -> list[DispatchPayload]:
    """Build dispatch payloads for all briefs with severity >= 4."""
    corr_map = {c.cluster_id: c for c in correlations}
    payloads = []
    for brief in briefs:
        if brief.severity_score < 4:
            continue
        corr = corr_map.get(brief.cluster_id)
        if not corr:
            continue
        payloads.append(build_dispatch(brief, corr))
    return payloads
