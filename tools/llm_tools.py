"""
LLM routing layer — Ollama (local), Claude (Anthropic), OpenAI, Google Gemini.

Provider selection priority:
  1. LLM_PROVIDER env var — explicit: ollama | claude | openai | google
  2. Auto-detect first available API key:
       ANTHROPIC_API_KEY → claude
       OPENAI_API_KEY    → openai
       GOOGLE_API_KEY    → google
  3. Default: ollama (no key required, runs local model)

Model overrides:
  STREETSENSE_MODEL  — Ollama model     (default: gemma4:latest, see config.py)
  CLAUDE_MODEL       — Anthropic model  (default: claude-haiku-4-5-20251001)
  OPENAI_MODEL       — OpenAI model     (default: gpt-4o-mini)
  GOOGLE_MODEL       — Gemini model     (default: gemini-2.0-flash)
"""

import json
import logging
import os

import requests

from config import MODEL, OLLAMA_BASE_URL
from specs.prompts import CORRELATION_SYSTEM_PROMPT, BRIEFING_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"

_CLAUDE_DEFAULT = "claude-haiku-4-5-20251001"
_OPENAI_DEFAULT = "gpt-4o-mini"
_GOOGLE_DEFAULT = "gemini-2.0-flash"


def _active_provider() -> str:
    """Return the active LLM provider based on env vars."""
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in {"ollama", "claude", "openai", "google"}:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("GOOGLE_API_KEY"):
        return "google"
    return "ollama"


def active_provider_info() -> dict:
    """Return provider name and model string. Used by /health endpoint."""
    provider = _active_provider()
    models = {
        "ollama": MODEL,
        "claude": os.getenv("CLAUDE_MODEL", _CLAUDE_DEFAULT),
        "openai": os.getenv("OPENAI_MODEL", _OPENAI_DEFAULT),
        "google": os.getenv("GOOGLE_MODEL", _GOOGLE_DEFAULT),
    }
    return {"provider": provider, "model": models[provider]}


def call_llm(prompt: str, system: str = "", temperature: float = 0.2,
             json_mode: bool = False) -> str:
    """
    Send a prompt to the active LLM provider and return response text.
    Returns empty string on any failure — never raises.
    json_mode=True requests structured JSON output where supported natively.
    """
    provider = _active_provider()
    if provider == "claude":
        return _call_claude(prompt, system=system, temperature=temperature)
    if provider == "openai":
        return _call_openai(prompt, system=system, temperature=temperature,
                            json_mode=json_mode)
    if provider == "google":
        return _call_google(prompt, system=system, temperature=temperature,
                            json_mode=json_mode)
    return _call_ollama(prompt, system=system, temperature=temperature,
                        json_mode=json_mode)


def call_llm_json(prompt: str, system: str = "", temperature: float = 0.1) -> dict:
    """
    Call LLM and parse response as JSON.
    Strips markdown fences if present. Returns {} on any failure — never raises.
    """
    raw = call_llm(prompt, system=system, temperature=temperature, json_mode=True)
    if not raw:
        return {}
    try:
        cleaned = (
            raw.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s\nRaw response: %s", e, raw[:500])
        return {}


# ---------------------------------------------------------------------------
# Ollama (local, default)
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, system: str = "", temperature: float = 0.2,
                 json_mode: bool = False) -> str:
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
        return resp.json()["message"]["content"].strip()
    except Exception as e:
        logger.error("Ollama call failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Claude (Anthropic)
# ---------------------------------------------------------------------------

def _call_claude(prompt: str, system: str = "", temperature: float = 0.1) -> str:
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed — run: pip install anthropic")
        return ""
    try:
        client = anthropic.Anthropic()
        kwargs: dict = {
            "model": os.getenv("CLAUDE_MODEL", _CLAUDE_DEFAULT),
            "max_tokens": 2048,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        msg = client.messages.create(**kwargs)
        return msg.content[0].text.strip()
    except Exception as e:
        logger.error("Claude call failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def _call_openai(prompt: str, system: str = "", temperature: float = 0.1,
                 json_mode: bool = False) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed — run: pip install openai")
        return ""
    try:
        client = OpenAI()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": os.getenv("OPENAI_MODEL", _OPENAI_DEFAULT),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 2048,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error("OpenAI call failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------

def _call_google(prompt: str, system: str = "", temperature: float = 0.1,
                 json_mode: bool = False) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        logger.error(
            "google-generativeai package not installed — "
            "run: pip install google-generativeai"
        )
        return ""
    try:
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY", ""))
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        gen_config: dict = {
            "temperature": temperature,
            "max_output_tokens": 2048,
        }
        if json_mode:
            gen_config["response_mime_type"] = "application/json"

        model = genai.GenerativeModel(
            model_name=os.getenv("GOOGLE_MODEL", _GOOGLE_DEFAULT),
            generation_config=gen_config,
        )
        resp = model.generate_content(full_prompt)
        return resp.text.strip()
    except Exception as e:
        logger.error("Google Gemini call failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_correlation_prompt(cluster_summary: str) -> str:
    return f"{CORRELATION_SYSTEM_PROMPT}\n\nCluster to analyze:\n{cluster_summary}"


def build_briefing_prompt(correlation_summary: str, impact_summary: str) -> str:
    return (
        f"{BRIEFING_SYSTEM_PROMPT}\n\n"
        f"Correlation analysis:\n{correlation_summary}\n\n"
        f"Impact assessment:\n{impact_summary}"
    )
