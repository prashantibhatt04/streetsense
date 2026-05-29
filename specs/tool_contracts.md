# StreetSense — Tool Contracts (ACI)

Every tool exposed to an agent is narrow, typed, and documented here.
**Rule: each tool does exactly one thing. No tool takes more than 3 parameters.**

---

## Ingestion Tools

### `fetch_road_restrictions() → list[UnifiedEvent]`
Fetches live road restriction records from Toronto Open Data API.  
Error: returns `[]` — never raises.  
When to use: ONLY called by IngestionAgent once per poll cycle.

### `fetch_ttc_alerts() → list[UnifiedEvent]`
Fetches live TTC service alert records from GTFS-RT text endpoint.  
Error: returns `[]` — never raises.

### `fetch_utility_cuts(geocode_fn, limit) → list[UnifiedEvent]`
Fetches utility cut permit records. Requires geocode_fn for lat/lng resolution.  
Error: returns `[]` — never raises.

### `fetch_311_requests(geocode_fn, limit) → list[UnifiedEvent]`
Fetches 311 service requests (watermain breaks, flooding, sewer backup).  
Error: returns `[]` — never raises.

### `geocode_address(address) → tuple[float, float] | None`
Geocode an address to (lat, lng). Cache-first; falls back to demo_geocode.  
Rate limit: 1 call/second (Nominatim policy).  
Error: returns `None` — never raises.

### `write_events(events, db_path) → WriteResult`
Append-only write to SQLite. Skips duplicates (INSERT OR IGNORE).  
HITL: NOT required — append-only, no mutations.

---

## DB Tools

### `fetch_all_from_db(hours) → list[UnifiedEvent]`
Read all events from local SQLite within the last `hours` hours.  
Air-gapped operation — no external calls.  
When to use: primary feed function for daemon/DB mode.

### `lookup_historical_pattern(cascade_type, corridor) → HistoricalMatch`
Query pattern_memory for a matching past cascade on the same corridor.  
Returns `HistoricalMatch(match_found=False)` if nothing found — never raises.

### `db_event_counts() → dict`
Return per-source event counts + DB size. Used by dashboard status endpoint.

---

## Geo Tools

### `cluster_events(events, radius_metres, time_window_minutes) → list[ClusterCandidate]`
Group events into clusters. Returns only clusters with ≥ 2 events.  
Deterministic: no LLM. Pure haversine spatial math.

### `haversine_metres(lat1, lng1, lat2, lng2) → float`
Great-circle distance in metres between two lat/lng points.

---

## LLM Tools

### `call_llm_json(prompt, system, temperature) → dict`
Call Ollama and parse response as JSON. Strips markdown fences.  
Returns `{}` on any failure — never raises.  
Model: read from `config.MODEL` — never hardcoded.

### `build_correlation_prompt(cluster_summary) → str`
Build the full correlation analysis prompt using the locked prompt from `specs/prompts.py`.

### `build_briefing_prompt(correlation_summary, impact_summary) → str`
Build the full briefing prompt using the locked prompt from `specs/prompts.py`.

---

## Dispatch Tools

### `emit_dispatch_payload(brief, correlation, human_approved) → DispatchPayload`
Build and emit the structured dispatch payload.  
**HITL enforced**: raises `HumanApprovalRequired` if `human_approved=False`.  
In demo mode: auto-approved after 3-second pause.

### `format_dispatch_for_log(payload) → str`
One-line summary of a dispatch payload for the agent log.

---

## External Tools

### `fetch_bikeshare_nearby(lat, lng, radius_m) → list[dict]`
Fetch Bike Share Toronto stations via public GBFS feed. No API key required.  
Returns `[]` on any failure — never raises.

### `emit_slack_notification(brief, webhook_url) → bool`
Post operational brief to Slack via incoming webhook.  
Reads `SLACK_WEBHOOK_URL` from env if not passed directly.  
Returns `True` on HTTP 200, `False` on any failure — never raises.
