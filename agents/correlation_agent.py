import logging
from specs.data_contracts import ClusterCandidate, CorrelationResult
from tools.llm_tools import call_llm_json, build_correlation_prompt
from config import MODEL
from state import agent_log
from agents.impact_agent import TTC_ROUTES, extract_affected_routes

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 2


def summarise_cluster(cluster: ClusterCandidate) -> str:
    lines = [
        f"Cluster ID: {cluster.cluster_id}",
        f"Events: {len(cluster.events)}",
        f"Radius: {cluster.radius_metres:.0f}m",
        f"Time window: {cluster.time_window_minutes} minutes",
        "",
        "Events:",
    ]
    for e in cluster.events:
        lines.append(
            f"  - [{e.source.value}] {e.event_type.value} at {e.address} "
            f"(severity {e.severity_raw}) — {e.description[:80]}"
        )
    return "\n".join(lines)


_VALID_CASCADE_TYPES = {
    "watermain_to_road", "road_to_ttc", "watermain_to_road_to_ttc",
    "utility_to_road", "flooding_cascade", "unrelated",
}


def parse_llm_response(raw: dict, cluster_id: str) -> CorrelationResult | None:
    try:
        confidence = float(raw.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        cascade_type = raw.get("cascade_type", "unrelated")
        if cascade_type not in _VALID_CASCADE_TYPES:
            cascade_type = "unrelated"
        return CorrelationResult(
            cluster_id=cluster_id,
            is_causal=bool(raw.get("is_causal", False)),
            confidence=confidence,
            cascade_type=cascade_type,
            causal_chain=list(raw.get("causal_chain", [])),
            reasoning=str(raw.get("reasoning", "")),
            llm_model=MODEL,
        )
    except Exception as e:
        logger.warning("Failed to parse LLM response: %s", e)
        return None


def fallback_result(cluster_id: str, reason: str) -> CorrelationResult:
    return CorrelationResult(
        cluster_id=cluster_id,
        is_causal=False,
        confidence=0.0,
        cascade_type="unrelated",
        causal_chain=[],
        reasoning=f"Fallback: {reason}",
        llm_model=MODEL,
    )


def heuristic_correlation(cluster: ClusterCandidate) -> CorrelationResult:
    """Deterministic cascade detection from event types when LLM is unavailable."""
    types = {e.event_type.value for e in cluster.events}

    has_watermain = "watermain_break" in types
    has_road     = "road_closure" in types
    has_transit  = "transit_disruption" in types
    has_flooding = "flooding" in types
    has_utility  = "utility_work" in types

    if has_watermain and has_road and has_transit:
        cascade_type  = "watermain_to_road_to_ttc"
        causal_chain  = ["watermain_break → road_closure → transit_disruption"]
        confidence    = 0.80
    elif has_watermain and has_road:
        cascade_type  = "watermain_to_road"
        causal_chain  = ["watermain_break → road_closure"]
        confidence    = 0.80
    elif has_road and has_transit:
        cascade_type  = "road_to_ttc"
        causal_chain  = ["road_closure → transit_disruption"]
        confidence    = 0.80
    elif has_flooding:
        cascade_type  = "flooding_cascade"
        causal_chain  = ["flooding event"]
        confidence    = 0.80
    elif has_utility and has_road:
        cascade_type  = "utility_to_road"
        causal_chain  = ["utility_work → road_closure"]
        confidence    = 0.80
    else:
        return fallback_result(cluster.cluster_id, "No cascade pattern in event types")

    return CorrelationResult(
        cluster_id=cluster.cluster_id,
        is_causal=True,
        confidence=confidence,
        cascade_type=cascade_type,
        causal_chain=causal_chain,
        reasoning=f"Heuristic: event types {sorted(types)} match {cascade_type}",
        llm_model=MODEL,
    )


def predict_at_risk_routes(cluster: ClusterCandidate,
                           alerted_routes: list[str]) -> list[str]:
    """
    Find TTC routes that pass near the cluster centroid but have NOT
    yet filed a disruption alert. These are the cascade-at-risk routes.
    alerted_routes: route IDs already in active TTC alerts for this cluster.
    """
    from tools.geo_tools import haversine_metres
    RISK_RADIUS_M = 500

    at_risk = []
    # TTC_ROUTES maps street keyword → route ID + approximate corridor lat/lng
    # We approximate "near cluster" by checking if any event address mentions the street
    # and the route is not already alerted
    all_nearby = set()
    for event in cluster.events:
        text = f"{event.address} {event.description}".lower()
        for street, route in TTC_ROUTES.items():
            if street in text:
                all_nearby.add(route)

    for route in all_nearby:
        if route not in alerted_routes:
            at_risk.append(route)

    if at_risk:
        agent_log.append(
            f"Cascade risk: routes {at_risk} near cluster — no alert filed yet"
        )
    return sorted(at_risk)


def correlate_cluster(cluster: ClusterCandidate) -> CorrelationResult:
    """
    Analyse a ClusterCandidate and return a CorrelationResult.
    - Single-event clusters are immediately rejected as non-causal.
    - LLM is called with a structured prompt and JSON response expected.
    - Falls back to non-causal result if LLM fails or returns bad data.
    - Never raises.
    """
    if len(cluster.events) < 2:
        agent_log.append(f"Cluster {cluster.cluster_id[:12]}: single event — skipping LLM")
        return fallback_result(cluster.cluster_id, "Single-event cluster cannot be causal")

    summary = summarise_cluster(cluster)
    prompt = build_correlation_prompt(summary)

    agent_log.append(
        f"Sending cluster of {len(cluster.events)} events to {MODEL} for causal analysis…"
    )

    for attempt in range(MAX_ITERATIONS):
        try:
            raw = call_llm_json(prompt)
        except Exception as e:
            logger.warning("LLM call raised on attempt %d: %s", attempt + 1, e)
            continue
        if not raw:
            logger.warning("Empty LLM response on attempt %d", attempt + 1)
            continue
        result = parse_llm_response(raw, cluster.cluster_id)
        if result:
            agent_log.append(
                f"{MODEL}: is_causal={result.is_causal}, "
                f"confidence={result.confidence:.2f}, "
                f"cascade={result.cascade_type}"
            )
            # F6 — identify at-risk routes not yet alerted
            alerted = extract_affected_routes(cluster)
            at_risk = predict_at_risk_routes(cluster, alerted)
            return result.model_copy(update={"at_risk_routes": at_risk})

    agent_log.append(f"LLM failed after {MAX_ITERATIONS} attempts — using heuristic fallback")
    return heuristic_correlation(cluster)


def correlate_batch(clusters: list[ClusterCandidate]) -> list[CorrelationResult]:
    """Correlate all clusters. Never raises."""
    return [correlate_cluster(c) for c in clusters]
