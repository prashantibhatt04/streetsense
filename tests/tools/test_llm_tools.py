import pytest
from unittest.mock import patch, MagicMock
from tools.llm_tools import call_llm, call_llm_json, build_correlation_prompt, build_briefing_prompt


# --- call_llm ---

def test_call_llm_returns_string_on_success():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "  hello world  "}}
    mock_resp.raise_for_status = MagicMock()
    with patch("tools.llm_tools.requests.post", return_value=mock_resp):
        result = call_llm("test prompt")
    assert result == "hello world"


def test_call_llm_returns_empty_on_network_failure():
    with patch("tools.llm_tools.requests.post", side_effect=Exception("timeout")):
        result = call_llm("test prompt")
    assert result == ""


def test_call_llm_returns_empty_on_bad_response():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"unexpected": "structure"}
    mock_resp.raise_for_status = MagicMock()
    with patch("tools.llm_tools.requests.post", return_value=mock_resp):
        result = call_llm("test prompt")
    assert result == ""


def test_call_llm_includes_system_message():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "ok"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("tools.llm_tools.requests.post", return_value=mock_resp) as mock_post:
        call_llm("user prompt", system="system prompt")
    payload = mock_post.call_args[1]["json"]
    roles = [m["role"] for m in payload["messages"]]
    assert "system" in roles


# --- call_llm_json ---

def test_call_llm_json_valid_json():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": '{"is_causal": true, "confidence": 0.9}'}}
    mock_resp.raise_for_status = MagicMock()
    with patch("tools.llm_tools.requests.post", return_value=mock_resp):
        result = call_llm_json("test")
    assert result["is_causal"] is True
    assert result["confidence"] == 0.9


def test_call_llm_json_strips_markdown_fences():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "```json\n{\"key\": \"value\"}\n```"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("tools.llm_tools.requests.post", return_value=mock_resp):
        result = call_llm_json("test")
    assert result["key"] == "value"


def test_call_llm_json_returns_empty_on_bad_json():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "not json at all"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("tools.llm_tools.requests.post", return_value=mock_resp):
        result = call_llm_json("test")
    assert result == {}


def test_call_llm_json_returns_empty_on_failure():
    with patch("tools.llm_tools.requests.post", side_effect=Exception("timeout")):
        result = call_llm_json("test")
    assert result == {}


# --- prompt builders ---

def test_build_correlation_prompt_contains_cluster():
    prompt = build_correlation_prompt("three events on Bathurst")
    assert "three events on Bathurst" in prompt
    assert "is_causal" in prompt
    assert "causal_chain" in prompt


def test_build_briefing_prompt_contains_both_summaries():
    prompt = build_briefing_prompt("correlation data", "impact data")
    assert "correlation data" in prompt
    assert "impact data" in prompt
    assert "headline" in prompt
    assert "recommended_actions" in prompt
