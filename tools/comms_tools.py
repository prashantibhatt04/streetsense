"""
Public communications tool — generates three draft communications when a
supervisor approves a confirmed cascade dispatch.

Called from dashboard/app.py:api_approve(). Never raises.
"""
import logging
from datetime import datetime, timezone

from specs.data_contracts import OperationalBrief, PublicCommunicationDraft
from tools.llm_tools import call_llm_json

logger = logging.getLogger(__name__)


def generate_public_comms(
    brief: OperationalBrief,
    affected_routes: list[str],
    estimated_commuters: int,
    ward: str = "unknown",
) -> PublicCommunicationDraft | None:
    """
    Generate three public communication drafts for a supervisor to review.
    Called automatically when a supervisor approves a cascade dispatch.
    Returns None if LLM fails — caller handles gracefully.
    Never raises.
    """
    routes_str = ", ".join(affected_routes) if affected_routes else "no specific routes identified"
    prompt = f"""You are drafting public communications for a Toronto city supervisor.

Incident: {brief.headline}
Severity: {brief.severity_score}/10
Affected TTC routes: {routes_str}
Estimated commuters: {estimated_commuters:,}
Ward: {ward}
Summary: {brief.body[:300]}

Generate three communications in this exact JSON:
{{
  "ttc_alert": "Under 280 chars. Start with route numbers. State the disruption and reason. Example format: '511 Bathurst: Service disruption at Bathurst/Prue due to watermain break. Expect delays. Use alternate routes.'",
  "councillor_email": "3-4 sentences. Professional tone. State what happened, what city departments are responding, estimated duration, and what the councillor's office should expect for constituent inquiries.",
  "social_post": "Under 280 chars. Plain language. No hashtags. No jargon. State what happened and what riders should do."
}}"""

    try:
        raw = call_llm_json(prompt)
        if not raw:
            return None
        ttc_alert = str(raw.get("ttc_alert", "")).strip()
        councillor_email = str(raw.get("councillor_email", "")).strip()
        social_post = str(raw.get("social_post", "")).strip()
        if not ttc_alert or not councillor_email or not social_post:
            logger.warning("generate_public_comms: LLM returned incomplete fields")
            return None
        return PublicCommunicationDraft(
            cluster_id=brief.cluster_id,
            generated_at=datetime.now(timezone.utc),
            ttc_alert=ttc_alert[:280],
            councillor_email=councillor_email,
            social_post=social_post[:280],
            generated_for_severity=brief.severity_score,
        )
    except Exception as e:
        logger.warning("generate_public_comms failed: %s", e)
        return None
