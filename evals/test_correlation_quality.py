"""
LLM-as-Judge eval for correlation agent output quality.
Calls Ollama — run deliberately, not on every change.
Usage: python3 -m pytest evals/test_correlation_quality.py -v
"""
import json
import pytest
import re
import requests
from config import MODEL, OLLAMA_BASE_URL
from specs.data_contracts import CorrelationResult

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"

JUDGE_SYSTEM = """You are evaluating the output of an urban operations AI system.
Score the given correlation analysis on each criterion from 0-10.
Respond in JSON only, no explanation outside the JSON."""


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

    # Try full parse first
    try:
        cleaned = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Extract first JSON object
    match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Extract any integer as score
    score_match = re.search(r'\b(10|[0-9])\b', raw)
    score = int(score_match.group()) if score_match else 5
    justification = raw[:120] if raw else "no response from model"
    print(f"  FALLBACK score={score} justification={justification[:60]}")
    return {"score": score, "justification": justification}

@pytest.fixture(scope="module")
def bathurst_correlation():
    """Run the real correlation agent against the Bathurst scenario."""
    import json
    from pathlib import Path
    from specs.data_contracts import UnifiedEvent, ClusterCandidate
    from agents.correlation_agent import correlate_cluster
    from tools.geo_tools import cluster_events

    path = Path(__file__).parent / "mock_data" / "oct2024_bathurst.json"
    data = json.loads(path.read_text())
    events = [UnifiedEvent(**e) for e in data["events"]]
    clusters = cluster_events(events, radius_metres=300, time_window_minutes=60)
    assert len(clusters) == 1
    return correlate_cluster(clusters[0])


def test_correlation_is_causal(bathurst_correlation):
    """The Bathurst scenario must be identified as causal."""
    assert bathurst_correlation.is_causal is True


def test_correlation_confidence_above_threshold(bathurst_correlation):
    """Confidence must be at least 0.70 for the demo scenario."""
    assert bathurst_correlation.confidence >= 0.70


def test_correlation_has_causal_chain(bathurst_correlation):
    """Causal chain must have at least 2 steps."""
    assert len(bathurst_correlation.causal_chain) >= 2


def test_correlation_mentions_watermain(bathurst_correlation):
    """Reasoning must reference the watermain break."""
    text = (bathurst_correlation.reasoning + " ".join(bathurst_correlation.causal_chain)).lower()
    assert any(w in text for w in ["watermain", "water main", "water"])


def test_correlation_mentions_transit(bathurst_correlation):
    """Reasoning or chain must reference transit disruption."""
    text = (bathurst_correlation.reasoning + " ".join(bathurst_correlation.causal_chain)).lower()
    assert any(w in text for w in ["ttc", "streetcar", "511", "transit", "divert"])


def test_correlation_reasoning_quality_judge(bathurst_correlation):
    context = f"is_causal: {bathurst_correlation.is_causal}\nconfidence: {bathurst_correlation.confidence}\ncausal_chain: {bathurst_correlation.causal_chain}\nreasoning: {bathurst_correlation.reasoning}"
    result = llm_judge(context, "Does the reasoning logically connect the watermain break to the road closure to the transit disruption? Score 0-10.")
    assert result["score"] >= 7, f"Reasoning quality too low: {result}"


def test_correlation_chain_specificity_judge(bathurst_correlation):
    context = f"causal_chain: {bathurst_correlation.causal_chain}"
    result = llm_judge(context, "Are the causal chain steps specific and actionable rather than vague? Score 0-10.")
    assert result["score"] >= 6, f"Chain specificity too low: {result}"
