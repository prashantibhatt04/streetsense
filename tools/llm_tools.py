import json
import logging
import requests
from config import MODEL, OLLAMA_BASE_URL
from specs.prompts import CORRELATION_SYSTEM_PROMPT, BRIEFING_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"


def call_llm(prompt: str, system: str = "", temperature: float = 0.2,
             json_mode: bool = False) -> str:
    """
    Send a prompt to Ollama and return the response text.
    Returns empty string on any failure — never raises.
    json_mode=True sets format='json' so Ollama guarantees complete valid JSON output.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "options": {"num_predict": 2048},
    }
    if json_mode:
        payload["format"] = "json"

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"].strip()
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return ""


def call_llm_json(prompt: str, system: str = "", temperature: float = 0.1) -> dict:
    """
    Call LLM and parse response as JSON.
    Uses Ollama JSON mode for guaranteed complete output; strips markdown fences as fallback.
    Returns empty dict on any failure — never raises.
    """
    raw = call_llm(prompt, system=system, temperature=temperature, json_mode=True)
    if not raw:
        return {}
    try:
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s\nRaw response: %s", e, raw[:500])
        return {}


def build_correlation_prompt(cluster_summary: str) -> str:
    """Build the full prompt for correlation analysis using the locked system prompt."""
    return f"{CORRELATION_SYSTEM_PROMPT}\n\nCluster to analyze:\n{cluster_summary}"


def build_briefing_prompt(correlation_summary: str, impact_summary: str) -> str:
    """Build the full prompt for briefing generation using the locked system prompt."""
    return (
        f"{BRIEFING_SYSTEM_PROMPT}\n\n"
        f"Correlation analysis:\n{correlation_summary}\n\n"
        f"Impact assessment:\n{impact_summary}"
    )
