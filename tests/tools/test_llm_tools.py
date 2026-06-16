import pytest
from unittest.mock import patch, MagicMock
from tools.llm_tools import (
    call_llm, call_llm_json,
    build_correlation_prompt, build_briefing_prompt,
    _active_provider, active_provider_info,
    _call_claude, _call_openai, _call_google,
)


@pytest.fixture(autouse=True)
def clear_provider_env(monkeypatch):
    """Ensure tests default to Ollama — never hit real LLM APIs."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# _active_provider — routing logic
# ---------------------------------------------------------------------------

def test_active_provider_defaults_to_ollama():
    assert _active_provider() == "ollama"


def test_active_provider_detects_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert _active_provider() == "claude"


def test_active_provider_detects_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert _active_provider() == "openai"


def test_active_provider_detects_google_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-test")
    assert _active_provider() == "google"


def test_active_provider_explicit_llm_provider_wins(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # would auto-select claude
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert _active_provider() == "openai"


def test_active_provider_ignores_unknown_value(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "llamacpp")  # not a valid provider
    assert _active_provider() == "ollama"


# ---------------------------------------------------------------------------
# active_provider_info
# ---------------------------------------------------------------------------

def test_active_provider_info_ollama():
    info = active_provider_info()
    assert info["provider"] == "ollama"
    assert isinstance(info["model"], str)
    assert len(info["model"]) > 0


def test_active_provider_info_claude(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    info = active_provider_info()
    assert info["provider"] == "claude"
    assert "claude" in info["model"]


def test_active_provider_info_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    info = active_provider_info()
    assert info["provider"] == "openai"
    assert "gpt" in info["model"]


def test_active_provider_info_google(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google")
    info = active_provider_info()
    assert info["provider"] == "google"
    assert "gemini" in info["model"]


# ---------------------------------------------------------------------------
# call_llm routing — Ollama (default)
# ---------------------------------------------------------------------------

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


def test_call_llm_sets_json_format_for_ollama():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "{}"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("tools.llm_tools.requests.post", return_value=mock_resp) as mock_post:
        call_llm("test", json_mode=True)
    payload = mock_post.call_args[1]["json"]
    assert payload.get("format") == "json"


# ---------------------------------------------------------------------------
# call_llm routing — cloud providers
# ---------------------------------------------------------------------------

def test_call_llm_routes_to_claude(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    with patch("tools.llm_tools._call_claude", return_value="claude response") as mock_fn:
        result = call_llm("test prompt")
    mock_fn.assert_called_once_with("test prompt", system="", temperature=0.2)
    assert result == "claude response"


def test_call_llm_routes_to_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with patch("tools.llm_tools._call_openai", return_value="openai response") as mock_fn:
        result = call_llm("test prompt", json_mode=True)
    mock_fn.assert_called_once_with("test prompt", system="", temperature=0.2,
                                    json_mode=True)
    assert result == "openai response"


def test_call_llm_routes_to_google(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google")
    with patch("tools.llm_tools._call_google", return_value="google response") as mock_fn:
        result = call_llm("test prompt")
    mock_fn.assert_called_once()
    assert result == "google response"


# ---------------------------------------------------------------------------
# Provider implementations — missing package returns empty string
# ---------------------------------------------------------------------------

def test_call_claude_returns_empty_if_package_missing():
    with patch.dict("sys.modules", {"anthropic": None}):
        result = _call_claude("test prompt")
    assert result == ""


def test_call_openai_returns_empty_if_package_missing():
    with patch.dict("sys.modules", {"openai": None}):
        result = _call_openai("test prompt")
    assert result == ""


def test_call_google_returns_empty_if_package_missing():
    with patch.dict("sys.modules", {"google.generativeai": None, "google": None}):
        result = _call_google("test prompt")
    assert result == ""


# ---------------------------------------------------------------------------
# Provider implementations — API call succeeds (mocked client)
# ---------------------------------------------------------------------------

def test_call_claude_sends_prompt_and_returns_text():
    mock_content = MagicMock()
    mock_content.text = "  watermain break causes road closure  "
    mock_msg = MagicMock()
    mock_msg.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        result = _call_claude("test prompt", system="system msg")

    assert result == "watermain break causes road closure"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["system"] == "system msg"
    assert call_kwargs["messages"][0]["content"] == "test prompt"


def test_call_claude_omits_system_when_empty():
    mock_content = MagicMock()
    mock_content.text = "response"
    mock_msg = MagicMock()
    mock_msg.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        _call_claude("test prompt")

    call_kwargs = mock_client.messages.create.call_args[1]
    assert "system" not in call_kwargs


def test_call_openai_sends_prompt_and_returns_text():
    mock_choice = MagicMock()
    mock_choice.message.content = "  openai says hello  "
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    mock_openai_mod = MagicMock()
    mock_openai_mod.OpenAI.return_value = mock_client

    with patch.dict("sys.modules", {"openai": mock_openai_mod}):
        result = _call_openai("test prompt", json_mode=True)

    assert result == "openai says hello"
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    assert call_kwargs["response_format"] == {"type": "json_object"}


def test_call_openai_no_json_format_when_json_mode_false():
    mock_choice = MagicMock()
    mock_choice.message.content = "response"
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    mock_openai_mod = MagicMock()
    mock_openai_mod.OpenAI.return_value = mock_client

    with patch.dict("sys.modules", {"openai": mock_openai_mod}):
        _call_openai("test prompt", json_mode=False)

    call_kwargs = mock_client.chat.completions.create.call_args[1]
    assert "response_format" not in call_kwargs


def test_call_google_sends_prompt_and_returns_text():
    mock_resp = MagicMock()
    mock_resp.text = "  gemini response  "
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_resp
    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model
    mock_google_pkg = MagicMock()
    mock_google_pkg.generativeai = mock_genai

    with patch.dict("sys.modules", {"google.generativeai": mock_genai,
                                    "google": mock_google_pkg}):
        result = _call_google("test prompt", system="sys", json_mode=True)

    assert result == "gemini response"
    gen_config = mock_genai.GenerativeModel.call_args[1]["generation_config"]
    assert gen_config["response_mime_type"] == "application/json"


def test_call_google_prepends_system_to_prompt():
    mock_resp = MagicMock()
    mock_resp.text = "response"
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_resp
    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model
    mock_google_pkg = MagicMock()
    mock_google_pkg.generativeai = mock_genai

    with patch.dict("sys.modules", {"google.generativeai": mock_genai,
                                    "google": mock_google_pkg}):
        _call_google("user prompt", system="system msg")

    prompt_sent = mock_model.generate_content.call_args[0][0]
    assert "system msg" in prompt_sent
    assert "user prompt" in prompt_sent


# ---------------------------------------------------------------------------
# Provider implementations — API call fails
# ---------------------------------------------------------------------------

def test_call_claude_returns_empty_on_api_error():
    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value.messages.create.side_effect = Exception("api error")
    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        result = _call_claude("test")
    assert result == ""


def test_call_openai_returns_empty_on_api_error():
    mock_openai_mod = MagicMock()
    mock_openai_mod.OpenAI.return_value.chat.completions.create.side_effect = Exception("rate limit")
    with patch.dict("sys.modules", {"openai": mock_openai_mod}):
        result = _call_openai("test")
    assert result == ""


def test_call_google_returns_empty_on_api_error():
    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value.generate_content.side_effect = Exception("quota exceeded")
    mock_google_pkg = MagicMock()
    mock_google_pkg.generativeai = mock_genai
    with patch.dict("sys.modules", {"google.generativeai": mock_genai,
                                    "google": mock_google_pkg}):
        result = _call_google("test")
    assert result == ""


# ---------------------------------------------------------------------------
# call_llm_json
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

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
