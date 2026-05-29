import logging
import uuid
from datetime import datetime, timezone
from state.schema import PipelineState
from ingestion.normalizer import normalize_batch
from tools.geo_tools import cluster_events, flood_cluster_pass
from config import FLOOD_CLUSTER_WINDOW_HOURS
from agents.correlation_agent import correlate_batch
from agents.impact_agent import assess_batch
from agents.briefing_agent import generate_batch, build_dispatch_batch
from agents.prediction_agent import predict_batch
from tools.dispatch_tools import save_dispatch
from state import agent_log

logger = logging.getLogger(__name__)


def ingest_node(state: PipelineState, fetch_fns: list) -> PipelineState:
    """
    Collect raw events from all feed functions and normalize them.
    fetch_fns is a list of callables that each return list[UnifiedEvent].
    """
    if state.is_stuck():
        return state.with_error("Circuit breaker: max iterations reached at ingest")

    all_events = []
    for fn in fetch_fns:
        try:
            events = fn()
            all_events.extend(events)
        except Exception as e:
            logger.error("Feed fetch failed: %s", e)
            state = state.with_error(str(e))

    normalized = normalize_batch(all_events)
    agent_log.append(f"Ingested {len(normalized)} events from {len(fetch_fns)} feed(s)")
    logger.info("Ingested %d events", len(normalized))
    return state.with_events(normalized)


def prediction_node(state: PipelineState) -> PipelineState:
    """
    Proactive cascade prediction — runs immediately after ingest on every
    watermain_break / flooding event, before clustering has occurred.
    Saves dispatch recommendations to dispatch_log.json for supervisor approval.
    """
    if state.is_stuck():
        return state.with_error("Circuit breaker: max iterations reached at predict")

    if not state.raw_events:
        return state.with_predictions([])

    predictions = predict_batch(state.raw_events)

    for pred in predictions:
        for dispatch in pred.recommended_dispatches:
            save_dispatch(dispatch)

    if predictions:
        total_dispatches = sum(len(p.recommended_dispatches) for p in predictions)
        agent_log.append(
            f"Prediction: {len(predictions)} cascade(s) predicted, "
            f"{total_dispatches} proactive dispatch(es) queued for approval"
        )

    return state.with_predictions(predictions)


def cluster_node(state: PipelineState) -> PipelineState:
    """Group nearby events into ClusterCandidates."""
    if state.is_stuck():
        return state.with_error("Circuit breaker: max iterations reached at cluster")

    if not state.raw_events:
        logger.info("No events to cluster")
        return state.with_clusters([])

    clusters = cluster_events(state.raw_events)

    # Second pass: group citywide flood events that didn't form a local 300m cluster
    clustered_ids = {e.event_id for c in clusters for e in c.events}
    flood_cluster = flood_cluster_pass(
        state.raw_events, clustered_ids, time_window_hours=FLOOD_CLUSTER_WINDOW_HOURS
    )
    if flood_cluster:
        clusters.append(flood_cluster)
        agent_log.append(
            f"Flood clustering: grouped {len(flood_cluster.events)} flood events into citywide cluster"
        )

    agent_log.append(
        f"Clustering: {len(clusters)} cluster(s) from {len(state.raw_events)} events"
    )
    logger.info("Formed %d clusters from %d events", len(clusters), len(state.raw_events))
    return state.with_clusters(clusters)


def correlate_node(state: PipelineState) -> PipelineState:
    """Run correlation agent on all clusters."""
    if state.is_stuck():
        return state.with_error("Circuit breaker: max iterations reached at correlate")

    if not state.clusters:
        return state.with_correlations([])

    correlations = correlate_batch(state.clusters)
    causal = sum(1 for c in correlations if c.is_causal)
    logger.info("%d/%d clusters are causal", causal, len(correlations))
    return state.with_correlations(correlations)


def impact_node(state: PipelineState) -> PipelineState:
    """Run impact agent on all correlated clusters."""
    if state.is_stuck():
        return state.with_error("Circuit breaker: max iterations reached at impact")

    if not state.correlations:
        return state.with_impacts([])

    impacts = assess_batch(state.clusters, state.correlations)
    logger.info("Assessed %d impacts", len(impacts))
    return state.with_impacts(impacts)


def brief_node(state: PipelineState) -> PipelineState:
    """Generate operational briefs for all assessed impacts."""
    if state.is_stuck():
        return state.with_error("Circuit breaker: max iterations reached at brief")

    if not state.impacts:
        return state.with_briefs([])

    briefs = generate_batch(state.clusters, state.correlations, state.impacts)
    logger.info("Generated %d briefs", len(briefs))
    return state.with_briefs(briefs)


def dispatch_node(state: PipelineState) -> PipelineState:
    """Build structured dispatch payloads for all high-severity briefs."""
    if state.is_stuck():
        return state.with_error("Circuit breaker: max iterations reached at dispatch")

    if not state.briefs:
        return state

    payloads = build_dispatch_batch(state.briefs, state.correlations)
    if payloads:
        agent_log.append(
            f"Dispatch: {len(payloads)} payload(s) ready — "
            f"priority={payloads[0].priority}, dept={payloads[0].target_department}"
        )
    return state.model_copy(update={"dispatch_payloads": payloads})


def run_pipeline(fetch_fns: list) -> PipelineState:
    """
    Execute the full StreetSense pipeline in sequence.
    Returns final PipelineState regardless of errors.
    Never raises.
    """
    agent_log.clear()
    state = PipelineState(
        run_id=f"run-{uuid.uuid4().hex[:8]}",
        started_at=datetime.now(timezone.utc),
    )

    try:
        state = ingest_node(state, fetch_fns)
        state = prediction_node(state)
        state = cluster_node(state)
        state = correlate_node(state)
        state = impact_node(state)
        state = brief_node(state)
        state = dispatch_node(state)
        agent_log.append("Pipeline complete.")
    except Exception as e:
        logger.error("Pipeline crashed: %s", e)
        state = state.with_error(f"Pipeline crash: {e}")
        agent_log.append(f"Pipeline error: {e}")

    return state
