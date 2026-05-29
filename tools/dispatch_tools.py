"""
ACI tools for dispatch operations.
Each tool does exactly one thing, takes ≤ 3 parameters, and never raises.
HITL enforced: emit_dispatch_payload raises HumanApprovalRequired if called
without prior approval — this is a hard safety gate.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from specs.data_contracts import OperationalBrief, DispatchPayload, CorrelationResult, DispatchRecommendation

DISPATCH_LOG_PATH = Path(__file__).parent.parent / "dispatch_log.json"

logger = logging.getLogger(__name__)


class HumanApprovalRequired(Exception):
    """Raised when emit_dispatch_payload is called without human_approved=True."""


def emit_dispatch_payload(
    brief: OperationalBrief,
    correlation: CorrelationResult,
    human_approved: bool,
) -> DispatchPayload:
    """
    Build and emit the structured dispatch payload.
    HITL enforced: raises HumanApprovalRequired if human_approved is False.
    In demo mode this is auto-approved after a visible pause in state/graph.py.
    In production: wait for supervisor button click in dashboard.
    """
    if not human_approved:
        raise HumanApprovalRequired(
            "Dispatch payload requires human approval before emission. "
            "Set human_approved=True in PipelineState first."
        )

    from agents.briefing_agent import build_dispatch
    payload = build_dispatch(brief, correlation)
    logger.info(
        "Dispatch emitted: action=%s priority=%s dept=%s",
        payload.action_type, payload.priority, payload.target_department,
    )
    return payload


def format_dispatch_for_log(payload: DispatchPayload) -> str:
    """Return a one-line summary of a dispatch payload for agent log."""
    return (
        f"DISPATCH [{payload.priority.upper()}] → {payload.target_department} "
        f"| {payload.action_type} | approval_required={payload.requires_human_approval}"
    )


# ---------------------------------------------------------------------------
# Proactive dispatch log — JSON file backed, keyed by dispatch_id
# ---------------------------------------------------------------------------

def _read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _write_log(records: list[dict], path: Path) -> None:
    path.write_text(json.dumps(records, indent=2))


def save_dispatch(recommendation: DispatchRecommendation,
                  path: Path = DISPATCH_LOG_PATH) -> None:
    """Upsert a DispatchRecommendation by dispatch_id. Never raises."""
    try:
        records = _read_log(path)
        new_rec = recommendation.model_dump(mode="json")
        for i, rec in enumerate(records):
            if rec.get("dispatch_id") == recommendation.dispatch_id:
                records[i] = new_rec
                _write_log(records, path)
                logger.info("Updated dispatch %s (%s)", recommendation.dispatch_id, recommendation.status)
                return
        records.append(new_rec)
        _write_log(records, path)
        logger.info("Saved dispatch %s (%s)", recommendation.dispatch_id, recommendation.status)
    except Exception as e:
        logger.error("save_dispatch failed: %s", e)


def approve_dispatch(dispatch_id: str,
                     path: Path = DISPATCH_LOG_PATH) -> DispatchRecommendation | None:
    """Set status to APPROVED for the given dispatch_id. Returns updated record or None."""
    try:
        records = _read_log(path)
        for rec in records:
            if rec.get("dispatch_id") == dispatch_id:
                rec["status"] = "APPROVED"
                rec["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
                _write_log(records, path)
                logger.info("Dispatch %s APPROVED", dispatch_id)
                return DispatchRecommendation(**rec)
        logger.warning("approve_dispatch: dispatch_id %s not found", dispatch_id)
        return None
    except Exception as e:
        logger.error("approve_dispatch failed: %s", e)
        return None


def reject_dispatch(dispatch_id: str,
                    path: Path = DISPATCH_LOG_PATH) -> DispatchRecommendation | None:
    """Set status to REJECTED for the given dispatch_id. Returns updated record or None."""
    try:
        records = _read_log(path)
        for rec in records:
            if rec.get("dispatch_id") == dispatch_id:
                rec["status"] = "REJECTED"
                rec["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
                _write_log(records, path)
                logger.info("Dispatch %s REJECTED", dispatch_id)
                return DispatchRecommendation(**rec)
        logger.warning("reject_dispatch: dispatch_id %s not found", dispatch_id)
        return None
    except Exception as e:
        logger.error("reject_dispatch failed: %s", e)
        return None


def get_pending_dispatches(path: Path = DISPATCH_LOG_PATH) -> list[DispatchRecommendation]:
    """Return all dispatches with status AWAITING_APPROVAL. Never raises."""
    try:
        records = _read_log(path)
        return [
            DispatchRecommendation(**r)
            for r in records
            if r.get("status") == "AWAITING_APPROVAL"
        ]
    except Exception as e:
        logger.error("get_pending_dispatches failed: %s", e)
        return []


def validate_dispatch_completeness(payload: DispatchPayload) -> list[str]:
    """
    Check a DispatchPayload for demo-readiness.
    Returns list of warning strings (empty = all good).
    """
    warnings = []
    if not payload.payload.get("headline"):
        warnings.append("dispatch.payload missing 'headline'")
    if not payload.payload.get("affected_routes"):
        warnings.append("dispatch.payload missing 'affected_routes'")
    if payload.payload.get("estimated_commuters", 0) == 0:
        warnings.append("dispatch.payload has zero commuters — check impact agent")
    return warnings
