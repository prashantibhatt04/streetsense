import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from tools.comms_tools import generate_public_comms
from specs.data_contracts import OperationalBrief, PublicCommunicationDraft


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_brief(severity: int = 7) -> OperationalBrief:
    return OperationalBrief(
        brief_id="brief-test-001",
        generated_at=datetime(2024, 10, 2, 12, 43, tzinfo=timezone.utc),
        cluster_id="cluster-bathurst-001",
        headline="Watermain break on Bathurst disrupts transit",
        body="Three related events on Bathurst St within 32 minutes. Water Services and TTC responding.",
        severity_score=severity,
        recommended_actions=["Deploy repair crew", "Activate 511 diversion"],
        source_event_count=3,
        estimated_commuters=14_000,
        affected_routes=["511"],
    )


VALID_LLM_RESPONSE = {
    "ttc_alert": "511 Bathurst: Service disruption at Bathurst/Prue due to watermain break. Expect delays. Use alternate routes.",
    "councillor_email": (
        "A watermain break at Bathurst St & Prue Ave has triggered an emergency road closure and "
        "511 streetcar diversion. Toronto Water and TTC Operations are responding. Estimated restoration "
        "within 3-4 hours. Constituent inquiries can be directed to 311."
    ),
    "social_post": (
        "Watermain break at Bathurst & Prue Ave is causing delays on the 511 Bathurst streetcar. "
        "Use alternate routes or the subway."
    ),
}


# ---------------------------------------------------------------------------
# generate_public_comms — success
# ---------------------------------------------------------------------------

def test_generate_public_comms_returns_draft_on_success():
    with patch("tools.comms_tools.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert isinstance(result, PublicCommunicationDraft)


def test_generate_public_comms_cluster_id_preserved():
    with patch("tools.comms_tools.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert result.cluster_id == "cluster-bathurst-001"


def test_generate_public_comms_severity_preserved():
    with patch("tools.comms_tools.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_public_comms(make_brief(severity=8), ["511"], 14_000)
    assert result.generated_for_severity == 8


def test_ttc_alert_under_280_chars():
    with patch("tools.comms_tools.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert len(result.ttc_alert) <= 280


def test_social_post_under_280_chars():
    with patch("tools.comms_tools.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert len(result.social_post) <= 280


def test_generate_public_comms_truncates_overlong_ttc_alert():
    long_response = dict(VALID_LLM_RESPONSE)
    long_response["ttc_alert"] = "x" * 400
    with patch("tools.comms_tools.call_llm_json", return_value=long_response):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert len(result.ttc_alert) == 280


def test_generate_public_comms_approved_by_supervisor_defaults_false():
    with patch("tools.comms_tools.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert result.approved_by_supervisor is False


def test_generate_public_comms_generated_at_is_utc():
    with patch("tools.comms_tools.call_llm_json", return_value=VALID_LLM_RESPONSE):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert result.generated_at.tzinfo is not None


# ---------------------------------------------------------------------------
# generate_public_comms — graceful failure
# ---------------------------------------------------------------------------

def test_generate_public_comms_returns_none_on_empty_llm_response():
    with patch("tools.comms_tools.call_llm_json", return_value={}):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert result is None


def test_generate_public_comms_returns_none_when_llm_raises():
    with patch("tools.comms_tools.call_llm_json", side_effect=Exception("LLM down")):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert result is None


def test_generate_public_comms_returns_none_on_incomplete_fields():
    with patch("tools.comms_tools.call_llm_json", return_value={"ttc_alert": "Some alert"}):
        result = generate_public_comms(make_brief(), ["511"], 14_000)
    assert result is None


# ---------------------------------------------------------------------------
# PublicCommunicationDraft — Pydantic model validation
# ---------------------------------------------------------------------------

def test_public_communication_draft_valid():
    draft = PublicCommunicationDraft(
        cluster_id="cluster-001",
        generated_at=datetime(2024, 10, 2, 12, 43, tzinfo=timezone.utc),
        ttc_alert="511 Bathurst: Delays due to watermain break.",
        councillor_email="A watermain break has occurred. City departments are responding.",
        social_post="Watermain break on Bathurst causing 511 delays.",
        generated_for_severity=7,
    )
    assert draft.cluster_id == "cluster-001"
    assert draft.approved_by_supervisor is False


def test_public_communication_draft_rejects_severity_above_10():
    with pytest.raises(Exception):
        PublicCommunicationDraft(
            cluster_id="cluster-001",
            generated_at=datetime(2024, 10, 2, 12, 43, tzinfo=timezone.utc),
            ttc_alert="Alert text",
            councillor_email="Email body",
            social_post="Social post",
            generated_for_severity=11,
        )


def test_public_communication_draft_rejects_severity_below_zero():
    with pytest.raises(Exception):
        PublicCommunicationDraft(
            cluster_id="cluster-001",
            generated_at=datetime(2024, 10, 2, 12, 43, tzinfo=timezone.utc),
            ttc_alert="Alert text",
            councillor_email="Email body",
            social_post="Social post",
            generated_for_severity=-1,
        )
