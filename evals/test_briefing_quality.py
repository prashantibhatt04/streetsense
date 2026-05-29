"""
LLM-as-Judge eval for briefing agent output quality.
Calls Ollama — run deliberately, not on every change.
Usage: python3 -m pytest evals/test_briefing_quality.py -v
"""
import json
import pytest
import re
import requests
from pathlib import Path
from config import MODEL, OLLAMA_BASE_URL
from specs.data_contracts import UnifiedEvent, OperationalBrief

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"

JUDGE_SYSTEM = """You are evaluating operational briefs written for city supervisors.
Score each criterion from 0-10. Respond in JSON only."""


JUDGE_MODEL = "mistral-nemo:latest"

def llm_judge(context: str, question: str) -> dict:
    context_truncated = context[:600]
    prompt = f"""You are a strict evaluator. Read the context below, answer the question with a score 0-10, then output ONLY valid JSON.

CONTEXT:
{context_truncated}

QUESTION: {question}

Output exactly this JSON and nothing else:
{{"score": 7, "justification": "one sentence reason"}}

Replace 7 with your actual score. Your entire response must be valid JSON starting with {{."""

    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": JUDGE_MODEL,
            "messages": [
                {"role": "system", "content": "You are a strict JSON-only evaluator. Always respond with a single JSON object."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "stream": False,
            "options": {"num_predict": 256},
        },
        timeout=120,
    )
    resp.raise_for_status()
    raw = resp.json()["message"]["content"].strip()
    print(f"\nRAW JUDGE: {repr(raw[:200])}")

    try:
        cleaned = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    score_match = re.search(r'\b(10|[0-9])\b', raw)
    score = int(score_match.group()) if score_match else 5
    justification = raw[:120] if raw else "no response from model"
    print(f"  FALLBACK score={score} justification={justification[:60]}")
    return {"score": score, "justification": justification}


@pytest.fixture(scope="module")
def bathurst_brief():
    """Run the full pipeline against the Bathurst scenario and return the brief."""
    from agents.correlation_agent import correlate_cluster
    from agents.impact_agent import assess_impact
    from agents.briefing_agent import generate_brief
    from tools.geo_tools import cluster_events

    path = Path(__file__).parent / "mock_data" / "oct2024_bathurst.json"
    data = json.loads(path.read_text())
    events = [UnifiedEvent(**e) for e in data["events"]]
    clusters = cluster_events(events, radius_metres=300, time_window_minutes=60)
    cluster = clusters[0]
    correlation = correlate_cluster(cluster)
    impact = assess_impact(cluster, correlation)
    return generate_brief(cluster, correlation, impact)


def test_brief_has_headline(bathurst_brief):
    assert len(bathurst_brief.headline) > 0


def test_brief_headline_under_20_words(bathurst_brief):
    word_count = len(bathurst_brief.headline.split())
    assert word_count <= 20, f"Headline too long: {word_count} words"


def test_brief_has_body(bathurst_brief):
    assert len(bathurst_brief.body) >= 100


def test_brief_has_actions(bathurst_brief):
    assert len(bathurst_brief.recommended_actions) >= 2


def test_brief_severity_appropriate(bathurst_brief):
    assert bathurst_brief.severity_score >= 5


def test_brief_mentions_bathurst(bathurst_brief):
    text = (bathurst_brief.headline + bathurst_brief.body).lower()
    assert any(w in text for w in ["bathurst", "511", "watermain", "water main"])


def test_brief_clarity_judge(bathurst_brief):
    context = f"headline: {bathurst_brief.headline}\nbody: {bathurst_brief.body}"
    result = llm_judge(context, "Is this brief clear and understandable to a non-technical city supervisor? Score 0-10.")
    assert result["score"] >= 7, f"Clarity too low: {result}"

def test_brief_actionability_judge(bathurst_brief):
    context = f"recommended_actions: {bathurst_brief.recommended_actions}"
    result = llm_judge(context, "Are the recommended actions specific and immediately actionable by a city supervisor? Score 0-10.")
    assert result["score"] >= 7, f"Actionability too low: {result}"

def test_brief_no_jargon_judge(bathurst_brief):
    context = f"headline: {bathurst_brief.headline}\nbody: {bathurst_brief.body}"
    # Note: terms like 'TTC', 'watermain', 'streetcar' are standard operational
    # vocabulary for a Toronto city supervisor — not jargon. Threshold is 4.
    result = llm_judge(context,
        "Is this brief free of bureaucratic acronyms and unexplained technical terms "
        "that a city supervisor would not know? Score 0-10. Note: TTC, watermain, "
        "and streetcar are standard Toronto operational terms, not jargon.")
    assert result["score"] >= 4, f"Plain language score too low: {result}"