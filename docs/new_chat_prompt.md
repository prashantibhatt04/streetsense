
I am building **StreetSense** — an agentic urban operations system for a hackathon this weekend. I need you to guide me through building it step by step using a spec-driven approach with unit tests at every stage.

## How we work together

- You explain what the next piece does and why it matters
- You write the implementation AND the unit tests for it together
- I run the tests in Claude Code and paste the results back here
- You read the test output and tell me: PASS (move on) or FAIL (here is the fix)
- We never move to the next step until all tests for the current step pass
- Never give me more than one step at a time
- Always wait for me to paste test output before continuing
- Explain every concept simply — I have never built an agentic AI system before

## The testing rule

Every file we create has a corresponding test file. We run the tests immediately after creating the file. If any test fails we fix it before writing anything new. This is non-negotiable.

Test files live in `tests/` and mirror the source structure:
- `ingestion/feeds/road_restrictions.py` → `tests/ingestion/test_road_restrictions.py`
- `agents/correlation_agent.py` → `tests/agents/test_correlation_agent.py`
- `tools/geo_tools.py` → `tests/tools/test_geo_tools.py`

We run tests with: `python3 -m pytest tests/ -v --tb=short`

---

## My Setup

- Mac M4, 32GB RAM, Wednesday night right now
- Ollama installed with qwen2.5:14b pulled and working
- Python 3.14 installed
- Claude Code installed
- Hackathon starts Friday evening — I have Wednesday night and all day Thursday to prepare
- At the hackathon I get an NVIDIA GB10 box running Nemotron locally

## The Key Constraint

Everything runs locally. No OpenAI, no Anthropic API, no cloud inference. All LLM calls go through Ollama on my machine during development, then through Ollama on the NVIDIA box at the event. One environment variable switches between models — nothing else changes.

```python
# config.py — the only line that changes at the event
MODEL = os.getenv("STREETSENSE_MODEL", "qwen2.5:14b")
# At the hackathon: export STREETSENSE_MODEL=nemotron-mini
```

---

## What StreetSense Does

Toronto generates infrastructure data across four separate systems that never talk to each other:
- Road closures and restrictions (live feed)
- TTC transit service alerts (live feed)
- Utility cut permits — excavation work (daily file)
- 311 citizen service requests (ongoing file)

StreetSense ingests all four feeds, detects when nearby events are causally related (a watermain break causes a road closure which disrupts a streetcar), scores severity, and generates a plain-language operational brief telling city supervisors what is happening and what to do.

**The demo story:** On October 2, 2024, three separate watermain break reports appeared on Bathurst St within one hour. The 511 Bathurst streetcar runs through that corridor. Three city departments had pieces of this story. Nobody coordinated. StreetSense would have caught it in real time.

---

## Data Sources (confirmed working from prior research)

**Road Restrictions — live feed, updates every few minutes:**
- URL: `https://secure.toronto.ca/opendata/cart/road_restrictions/v3?format=json`
- Has native lat/lng, description, work type, contractor name
- Known issue: returns malformed JSON with invalid backslash escapes — must fix with regex before parsing

**TTC Alerts — live feed, real-time:**
- URL: `https://gtfsrt.ttc.ca/alerts/all?format=text`
- Returns GTFS-RT in text format (not binary, not JSON)
- Contains route_id, stop_id, alert text

**Utility Cut Permits — daily file download:**
- URL: `https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/43cbc364-b673-49ca-b98b-8b99c5d5f6eb/resource/3bf43fcc-6c50-441c-862e-afbdb31d9a53/download/utility-cut-permits-data.json`
- Has address string, permit dates, client name, work type
- No lat/lng — needs geocoding via Nominatim

**311 Service Requests 2026 — file download, updated regularly:**
- URL: `https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/108c2bd1-6945-46f6-af92-02f5658ee7f7/resource/99b7f283-7345-4f5a-a126-d078ed4f3419/download/311-service-requests-2026.csv`
- Comes as a ZIP file containing SR2026.csv
- Has: Creation Date, Service Request Type, Intersection Street 1, Intersection Street 2
- No lat/lng — intersection strings need geocoding
- Key water event types: "Watermain-Possible Break", "Storm Event-Flooding",
  "Maintenance Hole - Overflowing", "Sewer main-Backup",
  "Catch Basin - Blocked / Flooding", "Road Water Ponding"

---

## Five Agentic Development Rules to Follow Throughout

1. **Strict ACI:** Every tool an agent uses is narrow and typed. Explicit docstrings explaining when and how to use it. Pydantic for all inputs and outputs.

2. **One agent, one job:** Never build one agent that does everything. Supervisor routes between specialized agents. No agent has more than 5 tools.

3. **Deterministic flow:** No open-ended while loops. LangGraph with explicit nodes and edges. Typed PipelineState object passed between every step.

4. **Resiliency:** max_iterations=5 ceiling on all agent loops. Circuit breaker if same node runs twice consecutively. All exceptions caught and returned as clean text so the agent can self-correct. Never crash on bad data.

5. **Isolated tests:** Test agents with frozen mock data only, never live feeds. LLM-as-Judge scoring for reasoning quality. Adversarial paths tested explicitly.

---

## Repository Structure

```
streetsense/
├── specs/
│   ├── data_contracts.py       ← Pydantic models — written first, tested first
│   ├── tool_contracts.md       ← ACI docstrings for every tool
│   └── prompts.py              ← All LLM prompts saved here after locking
│
├── ingestion/
│   ├── __init__.py
│   ├── feeds/
│   │   ├── __init__.py
│   │   ├── road_restrictions.py
│   │   ├── ttc_alerts.py
│   │   ├── utility_cuts.py
│   │   └── requests_311.py
│   ├── geocoder.py
│   ├── normalizer.py
│   └── store.py
│
├── agents/
│   ├── __init__.py
│   ├── supervisor.py
│   ├── ingestion_agent.py
│   ├── correlation_agent.py
│   ├── impact_agent.py
│   ├── briefing_agent.py
│   └── memory_agent.py
│
├── state/
│   ├── __init__.py
│   ├── schema.py
│   └── graph.py
│
├── tools/
│   ├── __init__.py
│   ├── db_tools.py
│   ├── geo_tools.py
│   ├── llm_tools.py
│   ├── dispatch_tools.py
│   └── external_tools.py
│
├── tests/                      ← mirrors source structure exactly
│   ├── conftest.py             ← shared fixtures for all tests
│   ├── specs/
│   │   └── test_data_contracts.py
│   ├── ingestion/
│   │   ├── feeds/
│   │   │   ├── test_road_restrictions.py
│   │   │   ├── test_ttc_alerts.py
│   │   │   ├── test_utility_cuts.py
│   │   │   └── test_requests_311.py
│   │   ├── test_geocoder.py
│   │   ├── test_normalizer.py
│   │   └── test_store.py
│   ├── agents/
│   │   ├── test_correlation_agent.py
│   │   ├── test_impact_agent.py
│   │   ├── test_briefing_agent.py
│   │   └── test_supervisor.py
│   ├── tools/
│   │   ├── test_geo_tools.py
│   │   ├── test_llm_tools.py
│   │   └── test_external_tools.py
│   └── integration/
│       ├── test_full_pipeline.py
│       └── test_replay_scenario.py
│
├── evals/
│   ├── mock_data/
│   │   ├── oct2024_bathurst.json     ← demo scenario
│   │   ├── queen_st_active.json      ← second scenario
│   │   ├── malformed_feed.json       ← adversarial: bad data
│   │   ├── outside_toronto.json      ← adversarial: wrong location
│   │   └── single_event.json         ← adversarial: not enough to cluster
│   ├── test_correlation_quality.py   ← LLM-as-Judge scoring
│   ├── test_briefing_quality.py      ← LLM-as-Judge scoring
│   └── test_adversarial.py           ← edge cases and refusal boundaries
│
├── dashboard/
│   ├── app.py
│   ├── replay.py
│   └── frontend/
│
├── config.py
├── main.py
├── pytest.ini
└── requirements.txt
```

---

## Test Categories We Use

**Unit tests** (`tests/`) — test one function or class in isolation, always with mock data, always fast (under 1 second each), never hit the network, never hit Ollama.

**Integration tests** (`tests/integration/`) — test multiple components working together, use the mock scenario JSON files, still no live network calls.

**Eval tests** (`evals/`) — test LLM reasoning quality using Ollama (these are the only tests that call qwen2.5:14b), score output quality, test adversarial inputs. These are slower — run them deliberately, not on every change.

**The rule for what goes where:**
- If it tests one function: unit test
- If it tests two or more components together: integration test
- If it calls an LLM: eval test

---

## Test Fixtures Strategy

All test data lives in `tests/conftest.py` as pytest fixtures. We never duplicate test data across test files. Key fixtures we will build:

```python
# tests/conftest.py — we will build this together

@pytest.fixture
def bathurst_watermain_event():
    """Single watermain break event at Bathurst & Prue Ave, Oct 2024."""
    # Returns a valid UnifiedEvent

@pytest.fixture  
def bathurst_cluster():
    """Three watermain events forming the Oct 2024 Bathurst cascade."""
    # Returns a ClusterCandidate with 3 events

@pytest.fixture
def causal_correlation():
    """A CorrelationResult where is_causal=True, confidence=0.87."""
    # Returns a CorrelationResult

@pytest.fixture
def malformed_road_restriction():
    """Raw dict with invalid backslash escapes, missing fields."""
    # Returns a raw dict that should fail gracefully

@pytest.fixture
def mississauga_event():
    """Event with coordinates outside Toronto bounds — should be rejected."""
    # Returns a raw dict with lat/lng in Mississauga

@pytest.fixture
def mock_db(tmp_path):
    """Temporary SQLite database for tests — deleted after each test."""
    # Returns path to a fresh test database
```

---

## What Each Test Must Verify

For every component we build, tests must cover:

**Happy path** — normal input produces correct output

**Edge cases:**
- Empty input (empty list, empty string, None)
- Minimum valid input (1 event, shortest address)
- Maximum valid input (large batch of events)

**Adversarial cases:**
- Malformed data (bad JSON, missing required fields)
- Out-of-bounds data (coordinates outside Toronto)
- Wrong types (string where int expected)

**Resiliency cases:**
- Network failure (mocked — function returns empty list, not exception)
- DB write failure (mocked — returns WriteResult with failure_count > 0)
- LLM timeout (mocked — returns fallback CorrelationResult)

---

## Timeline

**Wednesday night (tonight):**
- Phase 0: repo setup, pytest config, conftest.py, Pydantic models + tests

**Thursday morning:**
- Phase 1: data layer — all four feeds + tests, geocoder + tests, store + tests

**Thursday afternoon:**
- Phase 2: LLM prompt engineering + eval tests, correlation agent + tests

**Thursday evening:**
- Phase 3: impact agent + tests, briefing agent + tests, LangGraph graph + integration tests

**Friday daytime:**
- Phase 4: adversarial evals, dashboard backend + tests, replay mode
- Final pre-event checklist: all tests green

**Friday evening (event starts):**
- Port to NVIDIA GB10, run full test suite on new hardware, fix any failures

---

## The Checkpoint Protocol

After every phase, before moving to the next one, run the full test suite and paste the output:

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

I will read the output and tell you:
- **GREEN across the board** — move to next phase
- **One or two failures** — here is the exact fix, re-run before moving on
- **Many failures** — something structural is wrong, let us debug before continuing

You paste the pytest output. I tell you what it means. We do not move forward until it is green.

---

## Start Here — Phase 0

We are starting with **Phase 0: Repository Setup and Specs**.

Phase 0 has four steps. After each step you paste the output and I confirm before giving you the next step.

**Step 0.1 — Create repo structure and install dependencies**
**Step 0.2 — Write pytest.ini and conftest.py with core fixtures**
**Step 0.3 — Write specs/data_contracts.py with all Pydantic models**
**Step 0.4 — Write tests/specs/test_data_contracts.py and run them**

Please start with Step 0.1.

Explain:
1. What Phase 0 accomplishes and why specs come before any implementation code
2. What the repo structure command does
3. The exact Claude Code command to run to create it

Then wait for me to paste the output.
