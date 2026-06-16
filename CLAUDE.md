# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Python Interpreter

Always use `/usr/local/bin/python3.14` — the system `python3` (Homebrew 3.14) lacks Flask and other dependencies.

```bash
/usr/local/bin/python3.14 -m pytest tests/ -v --tb=short        # full suite (391 tests)
/usr/local/bin/python3.14 -m pytest tests/agents/test_prediction_agent.py -v --tb=short  # single file
/usr/local/bin/python3.14 dashboard/app.py                       # start dashboard on :5001
/usr/local/bin/python3.14 main.py --mode db                      # single pipeline run from SQLite
/usr/local/bin/python3.14 main.py --mode daemon --interval 120   # continuous loop from DB
/usr/local/bin/python3.14 main.py --watch                        # continuous live feed polling (300s default)
/usr/local/bin/python3.14 main.py --watch --interval 60          # custom interval
/usr/local/bin/python3.14 -m evals.replay oct2024_bathurst       # replay demo scenario
/usr/local/bin/python3.14 -m scripts.seed_db                     # seed SQLite from live Toronto feeds
```

## LLM Backend

All agents call a local Ollama instance at `http://localhost:11434`. Default model: `gemma4:latest` (8B, slow — ~30-55s per call on this machine). Override via env vars:

```bash
STREETSENSE_MODEL=mistral-nemo:latest /usr/local/bin/python3.14 dashboard/app.py
OLLAMA_BASE_URL=http://other-host:11434 ...
```

All LLM calls use `tools/llm_tools.py:call_llm_json()` with `temperature=0.1` and `timeout=60s`. Most agents have `MAX_ITERATIONS = 2`; `briefing_agent` uses `MAX_ITERATIONS = 5`. All agents have a deterministic heuristic fallback — agents never raise even if Ollama is down.

**Heuristic fallback behaviour:** When the LLM fails or times out, `correlation_agent.py:heuristic_correlation()` reads event types from the cluster and maps them to a cascade type deterministically (e.g. `{watermain_break, road_closure, transit_disruption}` → `watermain_to_road_to_ttc`, confidence=0.80). This guarantees `is_causal=True` and `confidence >= 0.80` even with Ollama down.

**Known bug — `heuristic_correlation` utility_to_road path is dead code:** `heuristic_correlation()` checks `has_utility = "utility_cut" in types` but `EventType.UTILITY_WORK` has value `"utility_work"`. `"utility_cut"` is never in the type set, so the `utility_to_road` branch can never fire heuristically. The queen_st scenario falls back to `is_causal=False` when LLM is down instead of detecting the cascade. Fix: change `"utility_cut"` to `"utility_work"` in `correlation_agent.py:heuristic_correlation()`.

## Architecture

### Pipeline Flow

```
Toronto APIs / SQLite DB
        ↓
  ingest_node          — fetches + normalises UnifiedEvents from 4 feeds
        ↓
  prediction_node      — for each watermain_break/flooding event, runs
                         prediction_agent → PredictedCascade (proactive, pre-cluster)
                         saves DispatchRecommendations to dispatch_log.json
        ↓
  cluster_node         — groups spatially/temporally nearby events → ClusterCandidates
        ↓
  correlate_node       — LLM: "are these events causally related?" → CorrelationResult
                         falls back to heuristic_correlation() if LLM fails
        ↓
  impact_node          — deterministic severity score + LLM for duration/actions → ImpactAssessment
        ↓
  brief_node           — LLM: writes operational brief → OperationalBrief
        ↓
  dispatch_node        — builds DispatchPayload for supervisor HITL approval
                         (only for briefs with severity_score >= 4)
```

Orchestrated by `state/graph.py:run_pipeline()`. State is immutable (`PipelineState` in `state/schema.py`) — every node returns a new copy via `with_*()` methods.

`cluster_node` runs a **second flood-clustering pass** via `geo_tools.flood_cluster_pass()` using `FLOOD_CLUSTER_WINDOW_HOURS = 3` — groups citywide flooding/sewer_backup events that didn't form a local 300m cluster.

**Important:** `evals/replay.py`, `dashboard/replay.py`, and `dashboard/app.py:api_replay()` all call all nodes including `prediction_node` and `dispatch_node`. If you add a new node to `run_pipeline()`, also add it to all three replay paths.

`agents/supervisor.py:SupervisorAgent` wraps `run_pipeline()` with per-cycle logging and run counting — thin orchestration layer, does no reasoning itself.

`agents/memory_agent.py:MemoryAgent` — overnight batch agent (cron 02:00). Reads yesterday's `cluster_log` rows, writes/increments `pattern_memory` rows so `BriefingAgent` can surface historical matches. Run manually: `python3 -m agents.memory_agent`. Never runs during live pipeline.

### Key Design Contracts

**`specs/data_contracts.py`** — all Pydantic models. Single source of truth for data shapes. `state/schema.py` imports from here and owns `PipelineState` (the app uses `state.schema.PipelineState`, not the one in `specs/data_contracts.py` — both exist, the latter is legacy).

**`specs/prompts.py`** — locked LLM prompts. Never hardcode prompts in agents. All prompt edits go here. The correlation prompt requires causal_chain steps to name the specific event type, location, and time.

**`config.py`** — model name, Ollama URL, Toronto bounding box, clustering params, geocoding rate limits.

**`tools/dispatch_tools.py`** — two separate dispatch systems:
- `emit_dispatch_payload()` — confirmed cascade dispatch (requires `human_approved=True`, safety gate)
- `save/approve/reject/get_pending_dispatches()` — proactive predicted dispatch log backed by `dispatch_log.json`
  - `save_dispatch()` uses `model_dump(mode='json')` to serialize datetime fields correctly
  - `approve_dispatch()` / `reject_dispatch()` write `updated_at` timestamp on state change

### DispatchRecommendation model (`specs/data_contracts.py`)

Has `created_at` (default=UTC now) and `updated_at: Optional[datetime] = None`. When serializing to JSON always use `model_dump(mode='json')` — plain `model_dump()` leaves datetime objects unserialised and will crash `json.dumps`.

### Data Sources (5 Toronto feeds)

| Feed | Module | Notes |
|---|---|---|
| 311 service requests | `ingestion/feeds/requests_311.py` | CSV from open data; requires geocoding |
| Road restrictions | `ingestion/feeds/road_restrictions.py` | JSON, has native lat/lng |
| TTC alerts | `ingestion/feeds/ttc_alerts.py` | GTFS-RT protobuf |
| Utility cuts | `ingestion/feeds/utility_cuts.py` | CSV; requires geocoding |
| TTC vehicle positions | `ingestion/feeds/ttc_vehicles.py` | Umoiq/NextBus JSON; polled every 30s by dashboard `/api/vehicles` for live map animation |

Geocoding uses Nominatim with a 1s rate limit (`NOMINATIM_RATE_LIMIT`). Results cached in `geocode_cache.json`.

### Route Extraction — Important Fix

`agents/impact_agent.py:extract_affected_routes()` uses `_primary_street()` to parse only the **primary street** from addresses like `"King St W at Bathurst St"`. This prevents cross-streets from being incorrectly flagged as affected routes. `predict_at_risk_routes()` in `correlation_agent.py` intentionally still scans the full address — cross-streets are legitimate at-risk candidates.

### Clustering Parameters (`config.py`)

- `CLUSTER_RADIUS_M = 300` — events within 300m form a candidate cluster
- `CLUSTER_WINDOW_HOURS = 1` — events within 1 hour are considered related
- Single-event clusters skip LLM correlation (immediately non-causal)

### SQLite Schema

`streetsense.db` has two key tables: `events` (raw unified events) and `cluster_log` (pipeline results including `human_approved` flag). `pattern_memory` stores historical cascade patterns used for historical match display in briefs.

## Testing

Tests are in `tests/` mirroring the source layout. `tests/conftest.py` has all shared fixtures — never duplicate fixtures in test files.

- `tests/specs/` — Pydantic model validation
- `tests/agents/` — agent logic with mocked `call_llm_json`
- `tests/tools/` — tool functions (dispatch uses `tmp_path` fixture for isolated JSON log)
- `tests/integration/` — full pipeline with mocked agent calls
- `tests/test_main.py` — run_once, run_daemon, --watch flag behaviour (9 tests)
- `evals/` — LLM-as-Judge quality tests, run deliberately (not in CI)

Always mock `call_llm_json` in agent tests — never hit Ollama in unit/integration tests.

## Dashboard

Flask app at `dashboard/app.py`, single template at `dashboard/templates/index.html`.

Key API endpoints:
- `GET /api/replay?scenario=oct2024_bathurst` — runs full mock scenario (all events)
- `GET /api/replay-phase?scenario=X&phase=N` — runs scenario up to phase N; returns `phase_info` + `confirmed_predictions`
- `GET /api/db` — runs pipeline from SQLite
- `GET /api/state` — runs live pipeline against Toronto APIs
- `GET /api/status` — system status: last_run, total_cycles, active_briefs, pending_dispatches
- `GET /api/log` — recent agent log entries (polled every 2s during a run)
- `GET /api/db-status` — DB event counts, last modified time, size
- `GET /health` — health check: model, db_events, db_exists
- `POST /api/approve/<cluster_id>` / `POST /api/reject/<cluster_id>` — HITL for confirmed cascades (fires Slack on approve)
- `GET /api/approval-status` — current approval decisions for last run's clusters
- `POST /api/predict-approve/<dispatch_id>` / `POST /api/predict-reject/<dispatch_id>` — HITL for predicted dispatches
- `GET /api/pending-dispatches` — proactive dispatches awaiting approval
- `GET /api/heatmap` — pattern_memory corridor data as lat/lng/weight points for Leaflet.heat
- `GET /api/vehicles?routes=511,501` — real-time TTC vehicle positions (Umoiq/NextBus)
- `GET /api/geocode?address=...` — geocode free-text address to lat/lng within Toronto
- `POST /api/submit-311` — accept a manually-submitted 311 ticket, run full pipeline, return predictions + SR number

The dashboard has a system status strip (top of page) that polls `/api/status` every 30 seconds and shows last run time, next refresh countdown, cycle count, active briefs, and pending dispatches.

Mock scenarios live in `evals/mock_data/`. The primary demo scenario is `oct2024_bathurst.json` — a real Oct 2024 event where a Bathurst St watermain break cascaded to road closure + 511 streetcar diversion across three departments with no coordination.

## main.py Modes

```
python3 main.py                         # single live run, exits
python3 main.py --mode db               # single run from SQLite, exits
python3 main.py --mode daemon           # continuous DB loop (120s default)
python3 main.py --watch                 # continuous live feed loop (300s default)
python3 main.py --watch --interval 60   # custom interval
```

`run_daemon()` catches `KeyboardInterrupt` cleanly, logs cycle timestamps, and flags any brief IDs that are new since the previous cycle.
