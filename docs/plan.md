# StreetSense — Completion Plan
> Status: pipeline complete, 391 tests passing, three demo scenarios working
> Goal: publish to GitHub as a runnable product for anyone (Ollama or Claude API)

---

## Current State

| Component | Status |
|---|---|
| Full pipeline (ingest → predict → cluster → correlate → impact → brief → dispatch) | ✅ Done |
| Flask dashboard on port 5001 | ✅ Done |
| Three demo scenarios with phase replay | ✅ Done |
| 391 tests passing | ✅ Done |
| GTFS spatial lookup (235 routes, 9368 stops) | ✅ Done |
| HITL approve/reject for confirmed cascades and predicted dispatches | ✅ Done |
| Historical pattern memory (pattern_memory table) | ✅ Done |
| Real-time TTC vehicle positions | ✅ Done |
| 311 submit modal | ✅ Done |

---

## Local Dev Setup (M4 Mac Mini, 32GB)

### Pull qwen2.5:32b — one-time

```bash
ollama pull qwen2.5:32b
```

Takes ~5 min on first pull (~20GB). After that, start the dashboard with:

```bash
STREETSENSE_MODEL=qwen2.5:32b /usr/local/bin/python3.14 dashboard/app.py
```

### Why qwen2.5:32b

- 20GB resident in 32GB unified memory — no swapping, full Metal GPU acceleration
- Same model family as the prompts were validated against (qwen2.5:14b)
- Inference: ~3–8s/call vs 30–55s with gemma4:8b
- Reliably produces valid JSON with correct schema and specific street/department names

### Verify quality after switching

```bash
STREETSENSE_MODEL=qwen2.5:32b /usr/local/bin/python3.14 -m evals.replay oct2024_bathurst
STREETSENSE_MODEL=qwen2.5:32b /usr/local/bin/python3.14 -m evals.replay queen_st_active
```

Check: causal_chain names specific streets + times, brief headline ≤ 15 words with street name,
recommended_actions reference specific departments (Toronto Water, TTC Operations).

---

## GitHub Publication Plan

### Step 1 — Fix the known bug

`correlation_agent.py` line 95: `heuristic_correlation()` checks `"utility_cut" in types`
but `EventType.UTILITY_WORK` has value `"utility_work"`. The `utility_to_road` heuristic
branch is dead code — queen_st returns `is_causal=False` when Ollama is down.

```python
# correlation_agent.py — one character change
has_utility = "utility_work" in types   # was "utility_cut"
```

### Step 2 — Add Claude API backend to `tools/llm_tools.py`

This is what makes the repo runnable by anyone without a GPU. The change adds a second
backend path — Ollama remains default, Claude API activates when `ANTHROPIC_API_KEY` is set.

```python
# tools/llm_tools.py — full replacement

import json
import logging
import os
import requests
from config import MODEL, OLLAMA_BASE_URL
from specs.prompts import CORRELATION_SYSTEM_PROMPT, BRIEFING_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"

# Claude model to use when ANTHROPIC_API_KEY is set
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


def call_llm(prompt: str, system: str = "", temperature: float = 0.2,
             json_mode: bool = False) -> str:
    """Route to Claude API or Ollama based on which key is available."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return _call_claude(prompt, system=system, temperature=temperature)
    return _call_ollama(prompt, system=system, temperature=temperature,
                        json_mode=json_mode)


def call_llm_json(prompt: str, system: str = "", temperature: float = 0.1) -> dict:
    """Call LLM and parse response as JSON. Returns {} on any failure."""
    raw = call_llm(prompt, system=system, temperature=temperature, json_mode=True)
    if not raw:
        return {}
    try:
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s\nRaw: %s", e, raw[:500])
        return {}


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


def _call_claude(prompt: str, system: str = "", temperature: float = 0.1) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic()
        kwargs: dict = {
            "model": CLAUDE_MODEL,
            "max_tokens": 2048,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        msg = client.messages.create(**kwargs)
        return msg.content[0].text.strip()
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return ""


def build_correlation_prompt(cluster_summary: str) -> str:
    return f"{CORRELATION_SYSTEM_PROMPT}\n\nCluster to analyze:\n{cluster_summary}"


def build_briefing_prompt(correlation_summary: str, impact_summary: str) -> str:
    return (
        f"{BRIEFING_SYSTEM_PROMPT}\n\n"
        f"Correlation analysis:\n{correlation_summary}\n\n"
        f"Impact assessment:\n{impact_summary}"
    )
```

Add `anthropic` to requirements:
```
anthropic>=0.40.0
```

### Step 3 — Add `.env.example`

```bash
# .env.example

# --- LLM Backend (pick one) ---

# Option A: Local Ollama (default, no key needed)
# Install: https://ollama.com — then: ollama pull qwen2.5:32b
STREETSENSE_MODEL=qwen2.5:32b
# STREETSENSE_MODEL=qwen2.5:14b     # if you have 16GB RAM
# STREETSENSE_MODEL=mistral-nemo:latest   # lightweight fallback

# Option B: Claude API (runs on any machine, no GPU needed)
# Get key: https://console.anthropic.com
# ANTHROPIC_API_KEY=sk-ant-...
# CLAUDE_MODEL=claude-haiku-4-5-20251001   # fast + cheap (default)
# CLAUDE_MODEL=claude-sonnet-4-6           # best quality

# --- Optional integrations ---
# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
# OLLAMA_BASE_URL=http://localhost:11434
```

### Step 4 — Write README.md

Cover: what it does, why it matters (the three-department coordination gap),
quick-start for both Ollama and Claude API paths, demo scenario instructions,
and a screenshot of the dashboard.

Structure:
```
## What it does
## Demo (30 seconds)
## Quick start — Claude API (any machine)
## Quick start — Local Ollama (GPU recommended)
## Running the demo scenarios
## Architecture
## Development
```

### Step 5 — Pre-publish checklist

```bash
# Confirm 391 tests still pass after llm_tools.py change
/usr/local/bin/python3.14 -m pytest tests/ -q --tb=short

# Confirm Claude API path works end-to-end
ANTHROPIC_API_KEY=<key> python3 -c "
from tools.llm_tools import call_llm_json
print(call_llm_json('Respond: {\"ok\": true}'))
"

# Confirm Ollama path still works
STREETSENSE_MODEL=qwen2.5:32b /usr/local/bin/python3.14 -m evals.replay oct2024_bathurst

# Check .gitignore covers secrets and cache
cat .gitignore
# Must include: .env, geocode_cache.json, dispatch_log.json,
#               streetsense.db, gtfs_cache/, __pycache__/
```

---

## What "Complete" Looks Like

### For people who clone the repo

| Situation | Setup | Command |
|---|---|---|
| Has Anthropic API key, no GPU | `pip install -r requirements.txt` + set `ANTHROPIC_API_KEY` | `python3 dashboard/app.py` |
| Has Ollama + GPU | `ollama pull qwen2.5:32b` | `STREETSENSE_MODEL=qwen2.5:32b python3 dashboard/app.py` |
| Just wants to see the demo | Either setup above | Click "Bathurst Oct2024" on the dashboard |

### Quality bar before publishing

- [ ] Fix `heuristic_correlation` bug (`"utility_cut"` → `"utility_work"`)
- [ ] `call_llm_json()` routes to Claude API when `ANTHROPIC_API_KEY` is set
- [ ] All three demo scenarios produce clean output with qwen2.5:32b
- [ ] `.env.example` committed, `.env` gitignored
- [ ] `README.md` has quick-start for both paths
- [ ] `requirements.txt` includes `anthropic>=0.40.0`
- [ ] 391 tests still pass after llm_tools.py change

### Nice-to-have (not blocking)

- queen_st dispatch routing fix: `utility_to_road` → Toronto Water as primary dept
  (`briefing_agent.py:dept_map` and `_DISPATCH_ACTION`)
- queen_st prediction: merge GTFS + keyword lookup so 501 Queen is always returned
- Dashboard screenshot in README

---

## Fixed During Functional Testing — June 15, 2026

Seven bugs found and fixed through systematic CLI and endpoint testing (not code review).
Full suite is **428/428 passing** after all fixes.

1. **Dispatch ID collision (flood scenario)** — `_make_dispatch_id()` truncated
   `trigger_event_id` to 16 characters. The 8 events in `july2024_flood` share a 16-char
   prefix, so all their dispatch recommendations collapsed into ~5 IDs and silently
   overwrote each other in `dispatch_log.json`. Fixed by removing the truncation.

2. **`evals/replay.py` crash on `malformed_feed`** — that fixture uses a `"records"` key
   (for `evals/test_adversarial.py`'s ingestion-layer tests), not `"events"`. Running it
   through `evals.replay` raised an unhandled `KeyError` with a full traceback. Fixed with
   a graceful check and a message pointing to the correct test runner.

3. **Route hint short-circuit (queen_st 501)** — `_get_route_hint()` in
   `agents/prediction_agent.py` short-circuited on a non-empty GTFS spatial result before
   checking the keyword-based corridor map. At Queen/Roncesvalles, GTFS returned 505/29
   but not 501; the keyword map had 501 but was never reached. Fixed by merging both
   sources with deduplication. Resolves open issue #2 below.

4. **Approval persistence** — `/api/approve` and `/api/reject` attempted to write
   `human_approved=1` to `cluster_log`, a column that doesn't exist, silently swallowed by
   a bare `except: pass`. Rejections had no DB write at all. Human decisions existed only
   in memory, wiped on every pipeline run — no audit trail and `memory_agent.py` had no
   visibility into outcomes. Fixed by adding `human_decision`/`decision_at` columns with an
   idempotent migration for existing databases.

5. **Dispatch ID accumulation** — `_make_dispatch_id()` included the LLM's array index for
   each recommendation. Since LLM output ordering isn't stable across runs, the same trigger
   event + dispatch type could get a different index each run, producing near-duplicate
   entries that accumulated indefinitely in `dispatch_log.json`. Fixed by keying IDs on
   `trigger_event_id + dispatch_type` only, making saves idempotent.

6. **Daemon signal handling** — `run_daemon()` relied on `KeyboardInterrupt` propagating
   from `time.sleep()`. When run backgrounded (the Docker/Azure Container Apps scenario),
   bash sets the child's SIGINT to `SIG_IGN` and CPython skips installing the default
   handler, so `KeyboardInterrupt` can never fire. `SIGTERM` — what container orchestrators
   actually send on shutdown — had no handler at all and killed the process immediately with
   no log line. Found by testing the daemon as a backgrounded process: it ran 49 cycles
   over 44 minutes ignoring `kill -INT` before requiring `kill -KILL`. Fixed with explicit
   `signal.signal()` handlers for both SIGINT and SIGTERM and a flag-based loop that checks
   every second instead of sleeping the full interval.

7. **`/health` provider reporting** — the endpoint read `config.MODEL` directly instead of
   calling `active_provider_info()`, which existed with a docstring saying it was "used by
   /health" but was never wired in. So `/health` always reported the Ollama model name
   regardless of which provider `call_llm_json()` was actually routing to — a problem for
   Azure deployment monitoring once `ANTHROPIC_API_KEY` is set. Fixed by wiring
   `active_provider_info()` into the response; the endpoint now includes a `"provider"` field.

---

## Known Open Issues (carry forward)

1. **queen_st dispatch routing** — `utility_to_road` maps to Transportation Services,
   should be Toronto Water. Fix: `briefing_agent.py:dept_map` and `_DISPATCH_ACTION`.

2. ~~**queen_st prediction misses 501 Queen**~~ — fixed June 15, 2026 (see item 3 above).

3. **queen_st brief over-infers** — LLM sometimes says "road closed and blocks 501"
   before those events arrived in phase 2. Needs briefing prompt guardrail.

4. **`heuristic_correlation` utility_to_road dead code** — `"utility_cut"` in types
   should be `"utility_work"`. One-line fix in `correlation_agent.py:95`.
   Blocking for publish — fix in Step 1.
