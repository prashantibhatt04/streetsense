"""
StreetSense Dashboard — Flask server.

Routes:
  GET /              → dashboard UI
  GET /api/replay    → replay oct2024_bathurst from JSON scenario file
  GET /api/state     → run live pipeline against real Toronto APIs
  GET /api/db        → run pipeline from local SQLite DB (air-gapped)
  GET /api/db-status → DB health: event counts, last seed time, size
  GET /api/log       → recent agent log entries (poll every 2s during run)
"""
import json
import logging
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state.graph import run_pipeline, prediction_node, cluster_node, correlate_node, impact_node, brief_node, dispatch_node
from state.schema import PipelineState
from state import agent_log
from specs.data_contracts import UnifiedEvent
from ingestion.geocoder import geocode_address
from ingestion.feeds.road_restrictions import fetch_road_restrictions
from ingestion.feeds.ttc_alerts import fetch_ttc_alerts
from ingestion.feeds.utility_cuts import fetch_utility_cuts
from ingestion.feeds.requests_311 import fetch_311_requests
from tools.db_tools import (
    fetch_all_from_db, db_event_counts, write_cluster_result,
    ensure_cluster_log_decision_columns, DB_PATH,
)
from tools.external_tools import fetch_bikeshare_nearby, emit_slack_notification
from ingestion.feeds.ttc_vehicles import fetch_vehicle_positions
from config import STREET_COORDS

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

_pipeline_lock = threading.Lock()

MOCK_DATA_DIR = ROOT / "evals" / "mock_data"
MOCK_DATA = MOCK_DATA_DIR / "oct2024_bathurst.json"

# Maps Toronto 311 service type labels to (event_type, severity_raw)
_SERVICE_TYPE_MAP: dict[str, tuple[str, int]] = {
    "Watermain-Possible Break":           ("watermain_break", 3),
    "Watermain Break-Confirmed":          ("watermain_break", 4),
    "Catch Basin - Blocked / Flooding":   ("flooding", 2),
    "Road Water Ponding":                 ("flooding", 2),
    "Street Flooding - Major":            ("flooding", 4),
    "Maintenance Hole - Overflowing":     ("flooding", 3),
    "Sewer Main-Backup":                  ("sewer_backup", 3),
    "Roadway Maintenance - Pothole":      ("utility_work", 2),
    "Sidewalk Maintenance":               ("utility_work", 1),
    "Traffic Signal - Malfunctioning":    ("utility_work", 1),
}

# Keywords in predicted_impact strings that confirm a newly arrived event type
_CONFIRM_KEYWORDS: dict[str, list[str]] = {
    "road_closure":       ["road closure", "road closed", "street closed", "close", "lane"],
    "transit_disruption": ["ttc", "streetcar", "bus", "diversion", "transit", "route"],
    "utility_work":       ["utility", "crew", "contractor", "repair crew"],
    "watermain_break":    ["watermain", "water main", "break"],
    "flooding":           ["flood", "drainage", "stormwater", "catch basin"],
    "sewer_backup":       ["sewer", "wastewater", "overflow", "backup"],
}


def _detect_confirmations(
    state,
    new_event_types: list[str],
    phase_sim_offset: int,
    prev_offset: int,
) -> list[dict]:
    """Match predicted impacts against newly arrived event types this phase."""
    confirmed = []
    seen: set[tuple] = set()
    for pc in state.predicted_cascades:
        for new_type in new_event_types:
            keywords = _CONFIRM_KEYWORDS.get(new_type, [])
            for impact in pc.predicted_impacts:
                if any(kw in impact.lower() for kw in keywords):
                    key = (pc.trigger_event_id, new_type)
                    if key not in seen:
                        seen.add(key)
                        confirmed.append({
                            "trigger_event_id": pc.trigger_event_id,
                            "predicted_impact": impact,
                            "confirmed_by_type": new_type,
                            "minutes_after_prediction": phase_sim_offset - prev_offset,
                        })
                    break
    return confirmed


def _serialise_state(state: PipelineState, mode: str = "live") -> dict:
    briefs = []
    for b in state.briefs:
        hist = None
        if b.historical_match and b.historical_match.match_found:
            hist = {
                "similar_date": b.historical_match.similar_date,
                "corridor": b.historical_match.corridor,
                "uncoordinated_hours": b.historical_match.uncoordinated_hours,
                "outcome": b.historical_match.outcome,
                "confidence": b.historical_match.confidence,
            }
        briefs.append({
            "headline": b.headline,
            "severity": b.severity_score,
            "body": b.body,
            "actions": b.recommended_actions,
            "cluster_id": str(b.cluster_id),
            "estimated_commuters": b.estimated_commuters,
            "affected_routes": b.affected_routes,
            "at_risk_routes": b.at_risk_routes,
            "historical_match": hist,
            "resident_impact": {
                "score": b.resident_impact.score,
                "commuters_affected": b.resident_impact.commuters_affected,
                "nearby_hospitals": b.resident_impact.nearby_hospitals,
                "nearby_schools": b.resident_impact.nearby_schools,
                "is_peak_hours": b.resident_impact.is_peak_hours,
                "factors": b.resident_impact.factors,
            } if b.resident_impact else None,
        })

    clusters = []
    for c in state.clusters:
        bike_stations = fetch_bikeshare_nearby(c.centroid_lat, c.centroid_lng, radius_m=800)
        clusters.append({
            "cluster_id": str(c.cluster_id),
            "event_count": len(c.events),
            "event_types": list({e.event_type.value for e in c.events}),
            "centroid_lat": c.centroid_lat,
            "centroid_lng": c.centroid_lng,
            "radius_m": round(c.radius_metres, 1),
            "bike_stations": bike_stations,
        })

    events_by_type: dict[str, int] = {}
    for e in state.raw_events:
        events_by_type[e.event_type.value] = events_by_type.get(e.event_type.value, 0) + 1

    events_geo = [{
        "event_id": e.event_id,
        "lat": e.latitude, "lng": e.longitude,
        "event_type": e.event_type.value,
        "source": e.source.value,
        "address": e.address,
        "description": e.description[:120],
        "severity_raw": e.severity_raw,
    } for e in state.raw_events]

    dispatch = []
    for d in state.dispatch_payloads:
        dispatch.append({
            "action_type": d.action_type,
            "priority": d.priority,
            "target_department": d.target_department,
            "payload": d.payload,
            "requires_human_approval": d.requires_human_approval,
        })

    predicted_cascades = []
    for pc in state.predicted_cascades:
        predicted_cascades.append({
            "trigger_event_id": pc.trigger_event_id,
            "predicted_impacts": pc.predicted_impacts,
            "confidence": round(pc.confidence, 2),
            "reasoning": pc.reasoning,
            "recommended_dispatches": [
                {
                    "dispatch_id": d.dispatch_id,
                    "dispatch_type": d.dispatch_type,
                    "target_department": d.target_department,
                    "message": d.message,
                    "priority": d.priority,
                    "status": d.status,
                }
                for d in pc.recommended_dispatches
            ],
        })

    return {
        "mode": mode,
        "run_id": state.run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "events_ingested": len(state.raw_events),
            "clusters_found": len(state.clusters),
            "correlations": len(state.correlations),
            "impacts": len(state.impacts),
            "briefs_generated": len(state.briefs),
            "errors": len(state.errors),
            "max_severity": max((b.severity_score for b in state.briefs), default=0),
            "total_commuters": sum(b.estimated_commuters for b in state.briefs),
        },
        "events_by_type": events_by_type,
        "events_geo": events_geo,
        "clusters": clusters,
        "briefs": briefs,
        "dispatch": dispatch,
        "predicted_cascades": predicted_cascades,
        "errors": state.errors[:10],
    }


def _persist_results(state: PipelineState) -> None:
    global _last_state_briefs, _status
    _last_state_briefs = []          # reset approval cache for this run
    _status["last_run"] = datetime.now(timezone.utc).isoformat()
    _status["total_cycles"] += 1
    _status["events_ingested"] = len(state.raw_events)
    _status["active_briefs"] = len(state.briefs)
    from tools.dispatch_tools import get_pending_dispatches
    _status["pending_dispatches"] = len(get_pending_dispatches())
    _approvals.clear()               # new run = new approval decisions
    _comms_drafts.clear()            # new run = new comms drafts

    corr_map = {c.cluster_id: c for c in state.correlations}
    for brief in state.briefs:
        corr = corr_map.get(brief.cluster_id)
        write_cluster_result(
            cluster_id=brief.cluster_id,
            run_id=state.run_id,
            cascade_type=corr.cascade_type if corr else "unrelated",
            severity_score=brief.severity_score,
            brief_headline=brief.headline,
            brief_body=brief.body,
        )
        # Cache brief dict for HITL approval endpoint
        _last_state_briefs.append(brief.model_dump())


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/log")
def api_log():
    """Return recent agent log entries. Dashboard polls this every 2s during a run."""
    return jsonify({"entries": agent_log.peek()})


@app.route("/api/replay")
def api_replay():
    if not _pipeline_lock.acquire(blocking=False):
        return jsonify({"error": "Pipeline already running — please wait for it to finish"}), 429

    scenario = request.args.get("scenario", "oct2024_bathurst")
    scenario_file = MOCK_DATA_DIR / f"{scenario}.json"
    if not scenario_file.exists():
        scenario_file = MOCK_DATA  # fallback

    agent_log.clear()
    agent_log.append(f"Loading scenario: {scenario}…")
    try:
        data = json.loads(scenario_file.read_text())
        events = []
        for raw in data["events"]:
            try:
                events.append(UnifiedEvent(**raw))
            except Exception as e:
                logger.warning("Skipping event: %s", e)

        agent_log.append(f"Loaded {len(events)} events from scenario file")

        state = PipelineState(
            run_id="replay-oct2024_bathurst",
            started_at=datetime.now(timezone.utc),
        ).with_events(events)

        agent_log.append(f"Ingested {len(events)} events")
        state = prediction_node(state)
        state = cluster_node(state)
        state = correlate_node(state)
        state = impact_node(state)
        state = brief_node(state)
        state = dispatch_node(state)
        agent_log.append("Pipeline complete.")
        _persist_results(state)
        return jsonify(_serialise_state(state, mode="replay"))
    except Exception as e:
        logger.exception("Replay failed")
        agent_log.append(f"Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        _pipeline_lock.release()


@app.route("/api/db")
def api_db():
    if not _pipeline_lock.acquire(blocking=False):
        return jsonify({"error": "Pipeline already running — please wait for it to finish"}), 429
    try:
        agent_log.clear()
        agent_log.append("Reading events from local SQLite DB…")
        events = fetch_all_from_db(hours=72)
        if not events:
            return jsonify({
                "mode": "db", "warning": "No events in local DB. Run: python3 -m scripts.seed_db",
                "summary": {"events_ingested": 0, "clusters_found": 0, "correlations": 0,
                            "impacts": 0, "briefs_generated": 0, "errors": 0},
                "events_geo": [], "clusters": [], "briefs": [], "dispatch": [],
                "events_by_type": {}, "errors": [],
                "run_id": "db-empty", "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        state = run_pipeline([lambda: events])
        _persist_results(state)
        return jsonify(_serialise_state(state, mode="db"))
    except Exception as e:
        logger.exception("DB pipeline failed")
        return jsonify({"error": str(e)}), 500
    finally:
        _pipeline_lock.release()


@app.route("/api/state")
def api_state():
    if not _pipeline_lock.acquire(blocking=False):
        return jsonify({"error": "Pipeline already running — please wait for it to finish"}), 429
    try:
        feed_fns = [
            fetch_road_restrictions,
            fetch_ttc_alerts,
            lambda: fetch_utility_cuts(geocode_fn=geocode_address, limit=100),
            lambda: fetch_311_requests(geocode_fn=geocode_address, limit=100),
        ]
        state = run_pipeline(feed_fns)
        _persist_results(state)
        return jsonify(_serialise_state(state, mode="live"))
    except Exception as e:
        logger.exception("Live pipeline failed")
        return jsonify({"error": str(e)}), 500
    finally:
        _pipeline_lock.release()


# ---------------------------------------------------------------------------
# In-memory HITL approval state (keyed by cluster_id)
# In production this would be a DB table with user identity + audit log
# ---------------------------------------------------------------------------
_approvals: dict[str, dict] = {}   # cluster_id → {status, approved_at, brief}
_comms_drafts: dict = {}           # cluster_id → PublicCommunicationDraft (ephemeral)

# Status tracker for /api/status
_status: dict = {
    "last_run": None,
    "next_run": None,
    "total_cycles": 0,
    "events_ingested": 0,
    "active_briefs": 0,
    "pending_dispatches": 0,
}

# Cache the last pipeline state so /api/approve can access brief data
_last_state_briefs: list = []


def _store_briefs_for_hitl(briefs):
    """Called by every pipeline serialiser to keep brief cache current."""
    global _last_state_briefs
    _last_state_briefs = [b.model_dump() if hasattr(b, 'model_dump') else b for b in briefs]


@app.route("/api/approve/<cluster_id>", methods=["POST"])
def api_approve(cluster_id: str):
    """
    Supervisor approves the dispatch payload for this cluster.
    Fires Slack notification and logs approval with timestamp.
    """
    try:
        approved_at = datetime.now(timezone.utc).isoformat()
        _approvals[cluster_id] = {"status": "approved", "approved_at": approved_at}

        # Find the matching brief from the last pipeline run and fire Slack
        from specs.data_contracts import OperationalBrief
        matching = next(
            (b for b in _last_state_briefs if b.get("cluster_id") == cluster_id),
            None
        )
        slack_sent = False
        if matching:
            # Rebuild minimal OperationalBrief for Slack
            brief_obj = OperationalBrief(**{
                k: matching[k] for k in matching
                if k in OperationalBrief.model_fields
            })
            slack_sent = emit_slack_notification(brief_obj)
            agent_log.append(
                f"APPROVED by supervisor — dispatching to {matching.get('affected_routes', [])} "
                f"| Slack {'sent' if slack_sent else 'skipped (no webhook)'}"
            )

            # Generate public communication drafts
            from tools.comms_tools import generate_public_comms
            routes = matching.get("affected_routes", [])
            commuters = matching.get("estimated_commuters", 0)
            ward = str(matching.get("metadata", {}).get("ward", "unknown")) if matching.get("metadata") else "unknown"
            try:
                draft = generate_public_comms(brief_obj, routes, commuters, ward=ward)
                if draft:
                    _comms_drafts[cluster_id] = draft
                    agent_log.append(
                        f"Public comms drafted: TTC alert ({len(draft.ttc_alert)} chars), "
                        f"councillor email, social post"
                    )
            except Exception as e:
                logger.warning("Comms generation failed: %s", e)

        # Write approval to cluster_log
        import sqlite3
        try:
            conn = sqlite3.connect(str(DB_PATH))
            ensure_cluster_log_decision_columns(conn)
            conn.execute(
                "UPDATE cluster_log SET human_decision='approved', decision_at=? WHERE cluster_id=?",
                (approved_at, cluster_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("cluster_log approval write failed: %s", e)

        logger.info("Cluster %s APPROVED at %s (Slack: %s)", cluster_id, approved_at, slack_sent)
        return jsonify({
            "status": "approved",
            "cluster_id": cluster_id,
            "approved_at": approved_at,
            "slack_sent": slack_sent,
        })
    except Exception as e:
        logger.exception("Approval failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reject/<cluster_id>", methods=["POST"])
def api_reject(cluster_id: str):
    """Supervisor rejects the dispatch. Logs rejection, no Slack fired."""
    rejected_at = datetime.now(timezone.utc).isoformat()
    _approvals[cluster_id] = {"status": "rejected", "rejected_at": rejected_at}
    agent_log.append(f"REJECTED by supervisor — cluster {cluster_id[:12]} — no dispatch sent")

    import sqlite3
    try:
        conn = sqlite3.connect(str(DB_PATH))
        ensure_cluster_log_decision_columns(conn)
        conn.execute(
            "UPDATE cluster_log SET human_decision='rejected', decision_at=? WHERE cluster_id=?",
            (rejected_at, cluster_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("cluster_log rejection write failed: %s", e)

    logger.info("Cluster %s REJECTED at %s", cluster_id, rejected_at)
    return jsonify({"status": "rejected", "cluster_id": cluster_id, "rejected_at": rejected_at})


@app.route("/api/comms/<cluster_id>")
def api_comms(cluster_id: str):
    """Return the public communication drafts for an approved cluster."""
    draft = _comms_drafts.get(cluster_id)
    if not draft:
        return jsonify({"error": "No comms draft found for this cluster"}), 404
    return jsonify({
        "cluster_id": cluster_id,
        "ttc_alert": draft.ttc_alert,
        "councillor_email": draft.councillor_email,
        "social_post": draft.social_post,
        "generated_at": draft.generated_at.isoformat(),
        "char_counts": {
            "ttc_alert": len(draft.ttc_alert),
            "social_post": len(draft.social_post),
        },
    })


@app.route("/api/predict-approve/<dispatch_id>", methods=["POST"])
def api_predict_approve(dispatch_id: str):
    """Supervisor approves a proactive predicted dispatch recommendation."""
    from tools.dispatch_tools import approve_dispatch
    result = approve_dispatch(dispatch_id)
    if result is None:
        return jsonify({"error": f"dispatch_id {dispatch_id} not found"}), 404
    agent_log.append(f"APPROVED predicted dispatch {dispatch_id[:20]} → {result.target_department}")
    logger.info("Predicted dispatch %s APPROVED", dispatch_id)
    return jsonify({"status": "approved", "dispatch_id": dispatch_id,
                    "approved_at": datetime.now(timezone.utc).isoformat()})


@app.route("/api/predict-reject/<dispatch_id>", methods=["POST"])
def api_predict_reject(dispatch_id: str):
    """Supervisor rejects a proactive predicted dispatch recommendation."""
    from tools.dispatch_tools import reject_dispatch
    result = reject_dispatch(dispatch_id)
    if result is None:
        return jsonify({"error": f"dispatch_id {dispatch_id} not found"}), 404
    agent_log.append(f"REJECTED predicted dispatch {dispatch_id[:20]}")
    logger.info("Predicted dispatch %s REJECTED", dispatch_id)
    return jsonify({"status": "rejected", "dispatch_id": dispatch_id,
                    "rejected_at": datetime.now(timezone.utc).isoformat()})


@app.route("/api/pending-dispatches")
def api_pending_dispatches():
    """Return all proactive dispatches currently awaiting supervisor approval."""
    from tools.dispatch_tools import get_pending_dispatches
    pending = get_pending_dispatches()
    return jsonify({
        "pending": [p.model_dump() for p in pending],
        "count": len(pending),
    })


@app.route("/api/approval-status")
def api_approval_status():
    """Return current approval status for all clusters in last run."""
    return jsonify(_approvals)


@app.route("/api/heatmap")
def api_heatmap():
    """
    Return pattern_memory data as lat/lng/weight points for Leaflet.heat.
    Each point represents a corridor with a known historical cascade pattern.
    """
    try:
        from tools.db_tools import DB_PATH
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            """SELECT corridor, cascade_type, MAX(confidence) as conf, COUNT(*) as n
               FROM pattern_memory GROUP BY corridor ORDER BY conf DESC"""
        ).fetchall()
        conn.close()

        # Also pull actual cluster centroids from cluster_log if available
        conn = sqlite3.connect(str(DB_PATH))
        cluster_rows = conn.execute(
            "SELECT cascade_type, brief_headline, severity_score FROM cluster_log"
        ).fetchall()
        conn.close()

        # Map corridor keyword → approximate Toronto centroid
        CORRIDOR_COORDS = {k: v for k, v in STREET_COORDS.items()
                           if k in {"bathurst","queen","king","dundas","spadina",
                                    "college","bloor","yonge","st clair","eglinton"}}

        points = []
        for corridor, cascade_type, conf, occurrences in rows:
            coords = CORRIDOR_COORDS.get(corridor.lower())
            if not coords:
                # fuzzy match first keyword found in corridor string
                for kw, c in CORRIDOR_COORDS.items():
                    if kw in corridor.lower():
                        coords = c
                        break
            if coords:
                points.append({
                    "lat": coords[0],
                    "lng": coords[1],
                    "weight": round(float(conf), 3),
                    "corridor": corridor,
                    "cascade_type": cascade_type,
                    "occurrences": occurrences,
                })

        return jsonify({"points": points, "count": len(points)})
    except Exception as e:
        logger.exception("Heatmap failed")
        return jsonify({"points": [], "error": str(e)}), 500


@app.route("/health")
def health():
    """Standard health check endpoint for deployment monitoring."""
    from tools.db_tools import db_event_counts
    from tools.llm_tools import active_provider_info
    counts = db_event_counts()
    provider_info = active_provider_info()
    return jsonify({
        "status": "ok",
        "provider": provider_info["provider"],
        "model": provider_info["model"],
        "db_events": counts.get("total", 0),
        "db_exists": counts.get("db_exists", False),
    })


@app.route("/api/vehicles")
def api_vehicles():
    """
    Return real-time TTC vehicle positions for given route IDs.
    Query param: routes=511,501 (comma-separated)
    Polled by the dashboard every 30s to animate moving vehicles on map.
    """
    try:
        routes_param = request.args.get("routes", "")
        route_filter = [r.strip() for r in routes_param.split(",") if r.strip()] or None
        vehicles = fetch_vehicle_positions(route_filter=route_filter)
        return jsonify({"vehicles": vehicles, "count": len(vehicles)})
    except Exception as e:
        logger.exception("Vehicle fetch failed")
        return jsonify({"vehicles": [], "error": str(e)}), 500


@app.route("/api/db-status")
def api_db_status():
    try:
        counts = db_event_counts()
        db_path = ROOT / "streetsense.db"
        mtime = None
        if db_path.exists():
            mtime = datetime.fromtimestamp(
                db_path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        return jsonify({**counts, "last_modified": mtime})
    except Exception as e:
        return jsonify({"error": str(e), "total": 0}), 500


@app.route("/api/status")
def api_status():
    """System status: last run time, cycle count, active briefs, pending dispatches."""
    return jsonify(_status)


@app.route("/api/replay-phase")
def api_replay_phase():
    """
    Run a scenario for a specific phase index.
    Each phase is a time-slice of the scenario: only the events that have arrived so far.
    Returns standard pipeline state + phase_info metadata + confirmed_predictions list.
    """
    if not _pipeline_lock.acquire(blocking=False):
        return jsonify({"error": "Pipeline already running — please wait for it to finish"}), 429

    scenario = request.args.get("scenario", "queen_st_active")
    try:
        phase_idx = int(request.args.get("phase", 0))
    except ValueError:
        phase_idx = 0

    scenario_file = MOCK_DATA_DIR / f"{scenario}.json"
    if not scenario_file.exists():
        _pipeline_lock.release()
        return jsonify({"error": f"Scenario not found: {scenario}"}), 404

    agent_log.clear()
    try:
        data = json.loads(scenario_file.read_text())
        phases = data.get("phases")
        if not phases:
            _pipeline_lock.release()
            return api_replay()

        phase_idx = max(0, min(phase_idx, len(phases) - 1))
        phase = phases[phase_idx]
        prev_phase = phases[phase_idx - 1] if phase_idx > 0 else None

        phase_event_ids = set(phase["event_ids"])
        events = []
        for raw in data["events"]:
            if raw["event_id"] in phase_event_ids:
                try:
                    events.append(UnifiedEvent(**raw))
                except Exception as e:
                    logger.warning("Skipping event: %s", e)

        agent_log.append(f"Phase {phase_idx + 1}/{len(phases)}: {phase['label']}")
        agent_log.append(f"Loaded {len(events)} event(s) — sim T+{phase['sim_offset_min']}min ({phase['sim_time']})")

        state = PipelineState(
            run_id=f"replay-{scenario}-p{phase_idx}",
            started_at=datetime.now(timezone.utc),
        ).with_events(events)

        state = prediction_node(state)
        state = cluster_node(state)
        state = correlate_node(state)
        state = impact_node(state)
        state = brief_node(state)
        state = dispatch_node(state)
        agent_log.append("Pipeline complete.")

        _persist_results(state)
        result = _serialise_state(state, mode="replay-phase")

        result["phase_info"] = {
            "scenario": scenario,
            "current_phase": phase_idx,
            "total_phases": len(phases),
            "sim_time": phase["sim_time"],
            "sim_offset_min": phase["sim_offset_min"],
            "label": phase["label"],
            "description": phase["description"],
        }

        # Detect which predictions from earlier phases are confirmed by new events this phase
        new_event_types: list[str] = []
        if phase_idx > 0 and prev_phase:
            new_ids = set(phase.get("new_event_ids", []))
            for raw in data["events"]:
                if raw["event_id"] in new_ids:
                    try:
                        ev = UnifiedEvent(**raw)
                        new_event_types.append(ev.event_type.value)
                    except Exception:
                        pass

        result["confirmed_predictions"] = _detect_confirmations(
            state,
            new_event_types,
            phase["sim_offset_min"],
            prev_phase["sim_offset_min"] if prev_phase else 0,
        )

        return jsonify(result)
    except Exception as e:
        logger.exception("Phase replay failed")
        agent_log.append(f"Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        _pipeline_lock.release()


@app.route("/api/geocode")
def api_geocode():
    """Geocode a free-text address to lat/lng within Toronto bounds."""
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"error": "address parameter required"}), 400
    try:
        coords = geocode_address(address)
        if not coords:
            return jsonify({"error": "Could not geocode address within Toronto"}), 404
        return jsonify({"lat": coords[0], "lng": coords[1], "address": address})
    except Exception as e:
        logger.exception("Geocode failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/submit-311", methods=["POST"])
def api_submit_311():
    """
    Accept a manually-submitted 311 service request and run it through the full pipeline.
    Returns standard pipeline state (predictions + any briefs) plus the filed SR number.
    """
    if not _pipeline_lock.acquire(blocking=False):
        return jsonify({"error": "Pipeline already running — please wait for it to finish"}), 429
    try:
        body = request.get_json(force=True) or {}
        service_type = body.get("service_type", "Roadway Maintenance - Pothole")
        address = (body.get("address") or "").strip()
        lat = body.get("lat")
        lng = body.get("lng")
        description = (body.get("description") or service_type).strip()
        ward = (body.get("ward") or "").strip()

        if not lat or not lng:
            if not address:
                return jsonify({"error": "Provide address or lat/lng"}), 400
            coords = geocode_address(address)
            if not coords:
                return jsonify({"error": "Could not geocode address within Toronto. Try adding ', Toronto, ON'"}), 400
            lat, lng = coords

        event_type_str, severity = _SERVICE_TYPE_MAP.get(service_type, ("utility_work", 2))
        sr_id = f"SR-{datetime.now().strftime('%Y-%m-%d')}-{uuid.uuid4().hex[:6].upper()}"
        event_id = f"311-submit-{uuid.uuid4().hex[:8]}"

        event = UnifiedEvent(
            event_id=event_id,
            source="requests_311",
            event_type=event_type_str,
            latitude=float(lat),
            longitude=float(lng),
            address=address or f"{float(lat):.4f}, {float(lng):.4f}",
            description=description,
            severity_raw=severity,
            timestamp=datetime.now(timezone.utc),
            source_id=sr_id,
            metadata={"ward": ward, "status": "Open", "submitted_via": "dashboard"},
        )

        agent_log.clear()
        agent_log.append(f"311 ticket received: {service_type}")
        agent_log.append(f"Location: {address}")

        state = PipelineState(
            run_id=f"311-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(timezone.utc),
        ).with_events([event])

        state = prediction_node(state)
        state = cluster_node(state)
        state = correlate_node(state)
        state = impact_node(state)
        state = brief_node(state)
        state = dispatch_node(state)
        agent_log.append("Analysis complete.")

        _persist_results(state)
        result = _serialise_state(state, mode="311-submit")
        result["submitted_event"] = {
            "event_id": event_id,
            "service_request_id": sr_id,
            "service_type": service_type,
            "event_type": event_type_str,
            "address": address,
            "lat": float(lat),
            "lng": float(lng),
        }
        return jsonify(result)
    except Exception as e:
        logger.exception("311 submit failed")
        agent_log.append(f"Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        _pipeline_lock.release()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    if DB_PATH.exists():
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(str(DB_PATH))
        ensure_cluster_log_decision_columns(_conn)
        _conn.close()
    app.run(debug=True, port=5001)
