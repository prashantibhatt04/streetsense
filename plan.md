# StreetSense — Spec-Driven Development Plan
> Agentic Urban Operations System · Toronto Tech Week · NVIDIA Hackathon
> Target: Friday 6pm → Sunday 9am submission

---

## 0. Guiding Principles

Every decision in this plan follows five non-negotiable rules:

1. **Strict ACI** — every tool exposed to an agent is narrow, typed, and documented. No generic functions.
2. **SRP** — one agent, one job. A supervisor routes between them.
3. **Deterministic state machines** — no open-ended loops. Explicit nodes, typed State objects, HITL for mutations.
4. **Resiliency** — max_iterations ceilings, circuit breakers, exceptions returned as text feedback.
5. **Isolated evals** — mock infrastructure only. LLM-as-Judge scoring. Adversarial path testing.

---

## 1. Repository Structure

```
streetsense/
├── specs/                        # Written before any code
│   ├── data_contracts.py         # Pydantic models — source of truth
│   ├── tool_contracts.md         # ACI docstrings for every tool
│   └── agent_graph.md            # State machine diagram in text
│
├── ingestion/
│   ├── feeds/
│   │   ├── road_restrictions.py  # Single feed, single file
│   │   ├── ttc_alerts.py
│   │   ├── utility_cuts.py
│   │   └── requests_311.py
│   ├── geocoder.py               # Nominatim wrapper with cache
│   ├── normalizer.py             # Raw → UnifiedEvent
│   └── store.py                  # SQLite write layer
│
├── agents/
│   ├── supervisor.py             # Router — the only agent with broad view
│   ├── ingestion_agent.py        # Polls feeds, writes to store
│   ├── correlation_agent.py      # Clustering + LLM causal reasoning
│   ├── impact_agent.py           # Severity score + population metric
│   ├── briefing_agent.py         # Plain-language brief + JSON dispatch
│   └── memory_agent.py           # Overnight pattern learning
│
├── state/
│   ├── schema.py                 # PipelineState TypedDict
│   └── graph.py                  # LangGraph nodes + edges
│
├── tools/                        # ACI tools — narrow, typed, documented
│   ├── db_tools.py
│   ├── geo_tools.py
│   ├── llm_tools.py
│   ├── dispatch_tools.py
│   └── external_tools.py         # Bike Share, Green P
│
├── evals/
│   ├── mock_data/                # Frozen snapshots, never live
│   │   ├── oct2024_bathurst.json
│   │   └── queen_st_scenario.json
│   ├── test_correlation.py       # LLM-as-Judge scoring
│   ├── test_briefing.py
│   ├── test_adversarial.py       # Refusal boundary tests
│   └── judge_prompts.py          # Scoring rubrics
│
├── dashboard/
│   ├── app.py                    # FastAPI + WebSocket server
│   ├── frontend/                 # React + Mapbox GL
│   │   ├── Map.jsx
│   │   ├── Sidebar.jsx
│   │   ├── AgentLog.jsx
│   │   └── ClusterCard.jsx
│   └── replay.py                 # Scenario replay engine
│
├── config.py                     # All constants, thresholds, URLs
├── main.py                       # Entry point — starts async pipeline
└── requirements.txt
```

---

## 2. Phase 0 — Specs First (Before Any Code)
**Duration: Thursday evening, 2–3 hours**
**Rule: Nothing in `/agents/` or `/ingestion/` is written until Phase 0 is complete.**

### 2.1 Data Contracts (`specs/data_contracts.py`)

Write these Pydantic models first. They are the interface contract every component honors.

```python
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
from datetime import datetime
from enum import Enum

class EventSource(str, Enum):
    ROAD_RESTRICTION = "road_restriction"
    TTC_ALERT = "ttc_alert"
    UTILITY_CUT = "utility_cut"
    REQUEST_311 = "311_request"

class EventType(str, Enum):
    WATERMAIN_BREAK = "watermain_break"
    ROAD_CLOSURE = "road_closure"
    TTC_DISRUPTION = "ttc_disruption"
    UTILITY_EXCAVATION = "utility_excavation"
    FLOODING = "flooding"
    UNKNOWN = "unknown"

class UnifiedEvent(BaseModel):
    """Single normalized event from any feed. Immutable after creation."""
    event_id: str
    source: EventSource
    event_type: EventType
    lat: float = Field(..., ge=43.5, le=44.0)   # Toronto bounds
    lng: float = Field(..., ge=-79.7, le=-79.1)  # Toronto bounds
    address: str
    timestamp: datetime
    status: Literal["open", "in_progress", "closed"]
    description: str = Field(..., max_length=500)
    raw: dict  # Original record preserved

    @field_validator("lat", "lng")
    @classmethod
    def must_be_in_toronto(cls, v, info):
        # Rejects events outside Toronto bounding box entirely
        return v

class ClusterCandidate(BaseModel):
    """Output of geospatial clustering. Input to LLM reasoning."""
    cluster_id: str
    events: list[UnifiedEvent]
    centroid_lat: float
    centroid_lng: float
    radius_m: float
    time_window_hours: float
    event_type_set: list[EventType]

class CorrelationResult(BaseModel):
    """Structured LLM output. Enforced — not free text."""
    cluster_id: str
    is_causal: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    cascade_type: Literal[
        "watermain_to_road",
        "road_to_ttc",
        "watermain_to_road_to_ttc",
        "utility_to_road",
        "flooding_cascade",
        "unrelated"
    ]
    reasoning: str = Field(..., max_length=300)

class SeverityScore(BaseModel):
    """Deterministic scoring — no LLM involved."""
    cluster_id: str
    score: int = Field(..., ge=1, le=10)
    ttc_routes_affected: list[str]
    estimated_commuters: int
    active_permits_overlapping: int
    score_breakdown: dict  # Auditable

class HistoricalMatch(BaseModel):
    """From pattern_memory table."""
    match_found: bool
    similar_date: Optional[str]
    outcome: Optional[str]
    uncoordinated_hours: Optional[float]
    confidence: Optional[float]

class OperationalBrief(BaseModel):
    """Final output of briefing agent."""
    cluster_id: str
    severity: SeverityScore
    historical_match: Optional[HistoricalMatch]
    situation: str           # 1-2 sentences: what's happening
    root_cause: str          # 1 sentence: likely cause
    downstream_impacts: list[str]
    recommended_action: str  # What to do
    departments_to_notify: list[str]
    draft_coordination_message: str

class DispatchPayload(BaseModel):
    """Structured action output. Mock API contract."""
    action_type: Literal[
        "notify_department",
        "consolidate_311_crew",
        "suggest_ttc_short_turn",
        "surface_bike_share",
        "surface_parking"
    ]
    priority: Literal["low", "medium", "high", "critical"]
    target_department: str
    payload: dict            # Action-specific data
    requires_human_approval: bool = True  # HITL enforced

class PipelineState(BaseModel):
    """Typed state object passed between all graph nodes."""
    run_id: str
    triggered_at: datetime
    raw_events: list[UnifiedEvent] = []
    cluster_candidates: list[ClusterCandidate] = []
    correlation_results: list[CorrelationResult] = []
    severity_scores: list[SeverityScore] = []
    briefs: list[OperationalBrief] = []
    dispatch_payloads: list[DispatchPayload] = []
    errors: list[str] = []
    iteration_count: int = 0
    human_approved: bool = False
```

### 2.2 Tool Contracts (`specs/tool_contracts.md`)

Write the ACI docstring for every tool before implementing it. This forces you to define the interface before the implementation.

**Rule: Each tool does exactly one thing. No tool takes more than 3 parameters.**

```
TOOL: fetch_road_restrictions
Input: max_age_minutes: int
Output: list[dict] (raw JSON records)
When to use: ONLY called by IngestionAgent to poll the live feed.
             NEVER called by correlation or briefing agents.
Error behavior: Returns {"error": "...", "records": []} — never raises.
Max call frequency: once per 120 seconds (enforced in tool, not caller).

TOOL: geocode_address
Input: address: str, city: str = "Toronto"
Output: GeoPoint(lat, lng) | None
When to use: ONLY when a feed record lacks native lat/lng.
             Check cache first — never hit Nominatim for known address.
Error behavior: Returns None on failure. Caller must handle gracefully.
Rate limit: 1 call/second enforced internally.

TOOL: write_events_to_store
Input: events: list[UnifiedEvent]
Output: WriteResult(success_count, failure_count, errors)
When to use: ONLY called after Pydantic validation passes.
             NEVER called with raw unvalidated dicts.
HITL: Does NOT require approval — append-only, no mutations.

TOOL: cluster_events_by_proximity
Input: events: list[UnifiedEvent], radius_m: int, window_hours: float
Output: list[ClusterCandidate]
When to use: ONLY called by CorrelationAgent.
             Input must have >= 2 events or returns empty list.
Deterministic: No LLM involved. Pure spatial math.

TOOL: reason_about_cluster
Input: cluster: ClusterCandidate, historical_context: str
Output: CorrelationResult
When to use: ONLY called after cluster_events_by_proximity.
             historical_context is injected from pattern_memory — pass "" if none.
LLM: Calls Nemotron locally. Structured output enforced via Pydantic.
Max retries: 2. Returns CorrelationResult(is_causal=False) on failure.

TOOL: calculate_severity
Input: correlation: CorrelationResult, cluster: ClusterCandidate
Output: SeverityScore
When to use: ONLY on clusters where is_causal=True AND confidence > 0.6.
Deterministic: No LLM. Arithmetic over GTFS stop counts + permit overlaps.

TOOL: lookup_historical_pattern
Input: cascade_type: str, corridor: str
Output: HistoricalMatch
When to use: ONLY called by BriefingAgent before generating brief.
             Returns match_found=False if no pattern exists — never errors.

TOOL: generate_operational_brief
Input: cluster: ClusterCandidate, severity: SeverityScore, history: HistoricalMatch
Output: OperationalBrief
When to use: ONLY on severity.score >= 4.
LLM: Calls Nemotron. Structured output enforced. Max 400 tokens.

TOOL: emit_dispatch_payload
Input: brief: OperationalBrief
Output: DispatchPayload
When to use: ONLY after brief is generated AND human_approved=True.
HITL: REQUIRED. Will raise HumanApprovalRequired if called without approval.

TOOL: fetch_bikeshare_nearby
Input: lat: float, lng: float, radius_m: int = 500
Output: list[BikeStation(name, available_bikes, distance_m)]
When to use: ONLY when dispatch action_type = "surface_bike_share".

TOOL: fetch_parking_nearby  
Input: lat: float, lng: float, radius_m: int = 800
Output: list[ParkingLot(name, available_spaces, distance_m)]
When to use: ONLY when dispatch action_type = "surface_parking".
```

### 2.3 Agent Graph (`specs/agent_graph.md`)

```
NODES:
  START
  ingest_node          → IngestionAgent
  cluster_node         → CorrelationAgent (clustering only)
  reason_node          → CorrelationAgent (LLM reasoning)
  impact_node          → ImpactAgent
  brief_node           → BriefingAgent
  hitl_node            → Human approval gate
  dispatch_node        → DispatchAgent
  memory_node          → MemoryAgent (overnight only)
  END

EDGES (deterministic):
  START → ingest_node
  ingest_node → cluster_node  [condition: new_events_count > 0]
  ingest_node → END           [condition: new_events_count == 0]
  cluster_node → reason_node  [condition: clusters_found > 0]
  cluster_node → END          [condition: clusters_found == 0]
  reason_node → impact_node   [condition: any(is_causal AND confidence > 0.6)]
  reason_node → END           [condition: no causal clusters]
  impact_node → brief_node    [condition: any(severity.score >= 4)]
  impact_node → END           [condition: all scores < 4]
  brief_node → hitl_node      [always — HITL required before dispatch]
  hitl_node → dispatch_node   [condition: human_approved == True]
  hitl_node → END             [condition: human_approved == False]
  dispatch_node → END

CIRCUIT BREAKER:
  Any node: iteration_count >= 5 → END with error logged
  Any node: identical tool call twice in sequence → END with circuit_break=True
  Any node: exception caught → add to state.errors, continue to END

OVERNIGHT ONLY:
  START → memory_node → END
  Triggered by: cron at 02:00 local time
  Input: yesterday's cluster outcomes from SQLite
```

---

## 3. Phase 1 — Data Layer
**Duration: Thursday night / Friday morning**

### 3.1 Config First

```python
# config.py — all magic numbers live here, nowhere else
ROAD_RESTRICTIONS_URL = "https://secure.toronto.ca/opendata/cart/road_restrictions/v3?format=json"
TTC_ALERTS_URL = "https://gtfsrt.ttc.ca/alerts/all?format=text"
UTILITY_CUTS_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/43cbc364.../utility-cut-permits-data.json"
REQUESTS_311_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/108c2bd1.../311-service-requests-2026.json"

DB_PATH = "streetsense.db"
GEOCODE_CACHE_TABLE = "geocode_cache"
EVENTS_TABLE = "events"
PATTERN_MEMORY_TABLE = "pattern_memory"

CLUSTER_RADIUS_M = 400
CLUSTER_WINDOW_HOURS = 6.0
MIN_CONFIDENCE_THRESHOLD = 0.6
MIN_SEVERITY_FOR_BRIEF = 4
MAX_AGENT_ITERATIONS = 5
POLL_INTERVAL_ROAD = 120   # seconds
POLL_INTERVAL_TTC = 120
POLL_INTERVAL_PERMITS = 3600
NOMINATIM_RATE_LIMIT = 1.0  # seconds between calls

TORONTO_BOUNDS = {
    "lat_min": 43.5, "lat_max": 44.0,
    "lng_min": -79.7, "lng_max": -79.1
}

NEMOTRON_MODEL = "nemotron-mini"  # on GB10
DEV_MODEL = "qwen2.5:14b"        # on Mac during dev
```

### 3.2 Feed Implementations

Each feed file follows the same pattern:

```python
# ingestion/feeds/road_restrictions.py
import re, requests, certifi
from datetime import datetime
from typing import Optional
from config import ROAD_RESTRICTIONS_URL
from specs.data_contracts import UnifiedEvent, EventSource, EventType

def _fix_json(text: str) -> str:
    """Remove invalid backslash escapes from Toronto road restrictions API."""
    return re.sub(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})', '', text)

def _classify_event_type(work_type: str, description: str) -> EventType:
    desc = (work_type + description).lower()
    if "watermain" in desc or "water main" in desc: return EventType.WATERMAIN_BREAK
    if "toronto water" in desc or "sewer" in desc: return EventType.UTILITY_EXCAVATION
    return EventType.ROAD_CLOSURE

def fetch_raw() -> list[dict]:
    """
    Fetch raw road restriction records from Toronto live API.
    Handles malformed JSON escape sequences automatically.
    Returns empty list on any failure — never raises.
    """
    try:
        r = requests.get(ROAD_RESTRICTIONS_URL, timeout=10)
        text = _fix_json(r.text)
        data = json.loads(text)
        return data.get("Closure", [])
    except Exception as e:
        return []  # Caller handles empty list gracefully

def to_unified_event(raw: dict) -> Optional[UnifiedEvent]:
    """
    Convert a single raw road restriction record to UnifiedEvent.
    Returns None if lat/lng missing or out of Toronto bounds.
    Validation failure is silent — logged externally.
    """
    try:
        return UnifiedEvent(
            event_id=f"rr-{raw['id']}",
            source=EventSource.ROAD_RESTRICTION,
            event_type=_classify_event_type(
                raw.get("workEventType", ""),
                raw.get("description", "")
            ),
            lat=float(raw["latitude"]),
            lng=float(raw["longitude"]),
            address=raw.get("name", raw.get("road", "")),
            timestamp=datetime.fromtimestamp(
                int(raw["createdTime"]) / 1000
            ),
            status="open" if not raw.get("expired") else "closed",
            description=raw.get("description", "")[:500],
            raw=raw
        )
    except Exception:
        return None
```

Implement the same pattern for each feed. TTC requires protobuf text parsing with regex. 311 and utility cuts are JSON file downloads.

### 3.3 Geocoder with Cache

```python
# ingestion/geocoder.py
import time, sqlite3
from typing import Optional
from dataclasses import dataclass
from config import DB_PATH, GEOCODE_CACHE_TABLE, NOMINATIM_RATE_LIMIT

@dataclass
class GeoPoint:
    lat: float
    lng: float

_last_call = 0.0

def geocode(address: str, city: str = "Toronto") -> Optional[GeoPoint]:
    """
    Geocode an address via Nominatim with local SQLite cache.
    Cache-first: never hits network for previously seen addresses.
    Rate limited to 1 call/second. Returns None on any failure.
    """
    global _last_call
    key = f"{address},{city}".lower().strip()

    # Cache lookup first
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            f"SELECT lat, lng FROM {GEOCODE_CACHE_TABLE} WHERE address_key=?",
            (key,)
        ).fetchone()
        if row:
            return GeoPoint(lat=row[0], lng=row[1])

    # Rate limit
    elapsed = time.time() - _last_call
    if elapsed < NOMINATIM_RATE_LIMIT:
        time.sleep(NOMINATIM_RATE_LIMIT - elapsed)
    _last_call = time.time()

    try:
        import requests
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{address}, {city}", "format": "json", "limit": 1},
            headers={"User-Agent": "StreetSense/1.0"},
            timeout=5
        )
        results = r.json()
        if not results:
            return None
        pt = GeoPoint(lat=float(results[0]["lat"]), lng=float(results[0]["lon"]))

        # Write to cache
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {GEOCODE_CACHE_TABLE} VALUES (?,?,?)",
                (key, pt.lat, pt.lng)
            )
        return pt
    except Exception:
        return None
```

### 3.4 SQLite Store

```python
# ingestion/store.py
import sqlite3, json
from dataclasses import dataclass
from specs.data_contracts import UnifiedEvent
from config import DB_PATH, EVENTS_TABLE

@dataclass
class WriteResult:
    success_count: int
    failure_count: int
    errors: list[str]

def init_db():
    """Create all tables. Idempotent — safe to call on every startup."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                source TEXT, event_type TEXT,
                lat REAL, lng REAL, address TEXT,
                timestamp TEXT, status TEXT,
                description TEXT, raw TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS geocode_cache (
                address_key TEXT PRIMARY KEY,
                lat REAL, lng REAL
            );
            CREATE TABLE IF NOT EXISTS pattern_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cascade_type TEXT, corridor TEXT,
                event_signature TEXT,
                outcome TEXT, uncoordinated_hours REAL,
                followup_311_count INTEGER,
                confidence REAL,
                observed_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS cluster_log (
                cluster_id TEXT PRIMARY KEY,
                run_id TEXT, cascade_type TEXT,
                severity_score INTEGER,
                estimated_commuters INTEGER,
                brief_text TEXT,
                dispatch_json TEXT,
                human_approved INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

def write_events(events: list[UnifiedEvent]) -> WriteResult:
    """
    Append-only write. Never updates or deletes existing events.
    Skips duplicates silently. Returns WriteResult with error details.
    """
    success, failure, errors = 0, 0, []
    with sqlite3.connect(DB_PATH) as conn:
        for e in events:
            try:
                conn.execute(
                    f"""INSERT OR IGNORE INTO {EVENTS_TABLE}
                    (event_id,source,event_type,lat,lng,address,
                     timestamp,status,description,raw)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (e.event_id, e.source, e.event_type,
                     e.lat, e.lng, e.address,
                     e.timestamp.isoformat(), e.status,
                     e.description, json.dumps(e.raw))
                )
                success += 1
            except Exception as ex:
                failure += 1
                errors.append(str(ex))
    return WriteResult(success, failure, errors)
```

---

## 4. Phase 2 — Agent Implementation
**Duration: Friday night**

### 4.1 Supervisor (Router)

```python
# agents/supervisor.py
"""
The only agent with visibility across the full pipeline.
Responsibilities: receive trigger, delegate to specialized agents,
enforce iteration ceiling, handle circuit breaker.
Does NOT reason about events — only routes.
"""
from specs.data_contracts import PipelineState
from config import MAX_AGENT_ITERATIONS

class SupervisorAgent:
    def __init__(self, graph):
        self.graph = graph
        self._last_tool_call = None

    def run(self, trigger: str) -> PipelineState:
        state = PipelineState(
            run_id=_generate_run_id(),
            triggered_at=datetime.now()
        )

        for _ in range(MAX_AGENT_ITERATIONS):
            state.iteration_count += 1
            next_node = self.graph.get_next_node(state)

            if next_node == "END":
                break

            # Circuit breaker: identical consecutive node
            if next_node == self._last_tool_call:
                state.errors.append(f"Circuit breaker: repeated node {next_node}")
                break

            self._last_tool_call = next_node
            state = self.graph.execute_node(next_node, state)

        return state
```

### 4.2 Ingestion Agent

```python
# agents/ingestion_agent.py
"""
Single responsibility: poll feeds, validate, geocode, write to store.
Tools available: fetch_road_restrictions, fetch_ttc_alerts,
                 fetch_utility_cuts, fetch_311_requests,
                 geocode_address, write_events_to_store.
Does NOT cluster, reason, or generate briefs.
"""
import asyncio
from ingestion.feeds import road_restrictions, ttc_alerts, utility_cuts
from ingestion.geocoder import geocode
from ingestion.store import write_events
from ingestion.normalizer import normalize
from specs.data_contracts import UnifiedEvent

class IngestionAgent:
    async def run_cycle(self) -> list[UnifiedEvent]:
        """
        Single ingestion cycle. Fetches all feeds concurrently.
        Returns list of successfully validated new events.
        """
        raw_results = await asyncio.gather(
            asyncio.to_thread(road_restrictions.fetch_raw),
            asyncio.to_thread(ttc_alerts.fetch_raw),
            asyncio.to_thread(utility_cuts.fetch_raw),
            return_exceptions=True
        )

        events = []
        for feed_name, raw_records in zip(
            ["road_restrictions", "ttc_alerts", "utility_cuts"],
            raw_results
        ):
            if isinstance(raw_records, Exception):
                # Log but continue — resilient to single feed failure
                continue
            for raw in raw_records:
                event = normalize(feed_name, raw)
                if event is None:
                    continue
                # Geocode if needed
                if event.lat == 0.0:
                    pt = geocode(event.address)
                    if pt:
                        event = event.model_copy(
                            update={"lat": pt.lat, "lng": pt.lng}
                        )
                    else:
                        continue  # Skip ungeocodable events
                events.append(event)

        if events:
            write_events(events)
        return events
```

### 4.3 Correlation Agent

```python
# agents/correlation_agent.py
"""
Single responsibility: find causal relationships between events.
Two phases: (1) deterministic spatial clustering, (2) LLM reasoning.
Tools: cluster_events_by_proximity, reason_about_cluster.
Does NOT score severity or generate briefs.
Max LLM calls per cycle: len(clusters) * 2 retries = bounded.
"""
import json
from tools.geo_tools import cluster_by_proximity
from tools.llm_tools import call_nemotron_structured
from tools.db_tools import fetch_recent_events, lookup_historical_pattern
from specs.data_contracts import (
    ClusterCandidate, CorrelationResult, PipelineState
)
from config import CLUSTER_RADIUS_M, CLUSTER_WINDOW_HOURS, MIN_CONFIDENCE_THRESHOLD

CORRELATION_SYSTEM_PROMPT = """
You are an urban operations analyst for the City of Toronto.
You will be given a cluster of infrastructure events that occurred
near each other in space and time.

Determine if these events are causally related.

Rules:
- A watermain break OFTEN causes road closures within 2 hours
- A road closure ON a TTC route OFTEN causes service disruptions
- Utility excavation near a watermain break is NOT coincidence
- Unrelated event types (graffiti + watermain) are NOT causal

You MUST respond in valid JSON matching this exact schema:
{
  "is_causal": boolean,
  "confidence": float between 0 and 1,
  "cascade_type": one of [
    "watermain_to_road", "road_to_ttc",
    "watermain_to_road_to_ttc", "utility_to_road",
    "flooding_cascade", "unrelated"
  ],
  "reasoning": string under 300 characters
}

Respond with JSON only. No preamble. No explanation outside the JSON.
"""

class CorrelationAgent:
    def run(self, state: PipelineState) -> PipelineState:
        events = fetch_recent_events(hours=CLUSTER_WINDOW_HOURS)
        clusters = cluster_by_proximity(
            events, CLUSTER_RADIUS_M, CLUSTER_WINDOW_HOURS
        )
        state.cluster_candidates = clusters

        results = []
        for cluster in clusters:
            history = lookup_historical_pattern(
                cluster.event_type_set, cluster.centroid_lat, cluster.centroid_lng
            )
            result = self._reason(cluster, history)
            if result.confidence >= MIN_CONFIDENCE_THRESHOLD:
                results.append(result)

        state.correlation_results = results
        return state

    def _reason(self, cluster: ClusterCandidate, history: str) -> CorrelationResult:
        """
        Call Nemotron with structured output constraint.
        Retries once on parse failure. Returns is_causal=False on second failure.
        """
        prompt = self._build_prompt(cluster, history)
        for attempt in range(2):
            try:
                raw = call_nemotron_structured(
                    system=CORRELATION_SYSTEM_PROMPT,
                    user=prompt,
                    max_tokens=200
                )
                data = json.loads(raw)
                return CorrelationResult(cluster_id=cluster.cluster_id, **data)
            except Exception as e:
                if attempt == 1:
                    return CorrelationResult(
                        cluster_id=cluster.cluster_id,
                        is_causal=False,
                        confidence=0.0,
                        cascade_type="unrelated",
                        reasoning=f"Parse failure: {str(e)[:100]}"
                    )

    def _build_prompt(self, cluster: ClusterCandidate, history: str) -> str:
        events_text = "\n".join([
            f"- [{e.event_type}] {e.description[:100]} at {e.address} ({e.timestamp.strftime('%H:%M')})"
            for e in cluster.events
        ])
        hist_text = f"\nHistorical context: {history}" if history else ""
        return f"""
Cluster of {len(cluster.events)} events within {cluster.radius_m}m 
over {cluster.time_window_hours} hours in Toronto:

{events_text}
{hist_text}

Are these causally related? Respond in JSON.
"""
```

### 4.4 Impact Agent

```python
# agents/impact_agent.py
"""
Single responsibility: calculate severity score deterministically.
NO LLM involved. Pure arithmetic over cached static data.
Tools: calculate_severity, fetch_gtfs_stops_in_radius,
       fetch_ward_population, count_active_permits_in_radius.
"""
import sqlite3
from math import radians, cos, sin, asin, sqrt
from specs.data_contracts import CorrelationResult, ClusterCandidate, SeverityScore
from config import DB_PATH

# Pre-loaded on startup — never queried live
GTFS_STOPS = []      # Loaded from static GTFS file
WARD_POPULATION = {} # Loaded from Toronto census open data

class ImpactAgent:
    def score(
        self,
        correlation: CorrelationResult,
        cluster: ClusterCandidate
    ) -> SeverityScore:
        """
        Deterministic severity scoring. Auditable breakdown.
        Score 1-10 based on: event types, TTC impact,
        population affected, active permit overlaps.
        """
        breakdown = {}

        # Base score from cascade type
        type_scores = {
            "watermain_to_road_to_ttc": 4,
            "watermain_to_road": 3,
            "road_to_ttc": 3,
            "flooding_cascade": 4,
            "utility_to_road": 2,
            "unrelated": 0
        }
        base = type_scores[correlation.cascade_type]
        breakdown["cascade_type"] = base

        # TTC routes in radius
        ttc_routes = self._ttc_routes_in_radius(
            cluster.centroid_lat, cluster.centroid_lng, cluster.radius_m
        )
        ttc_bonus = min(len(ttc_routes), 3)
        breakdown["ttc_routes"] = ttc_bonus

        # Population metric
        commuters = self._estimate_commuters(
            cluster.centroid_lat, cluster.centroid_lng, cluster.radius_m
        )
        pop_bonus = min(commuters // 5000, 2)
        breakdown["population"] = pop_bonus

        # Active permit overlaps
        permits = self._count_permits_in_radius(
            cluster.centroid_lat, cluster.centroid_lng, cluster.radius_m
        )
        permit_bonus = min(permits, 1)
        breakdown["permits"] = permit_bonus

        # Confidence multiplier
        score = (base + ttc_bonus + pop_bonus + permit_bonus)
        score = round(score * correlation.confidence)
        score = max(1, min(score, 10))

        return SeverityScore(
            cluster_id=correlation.cluster_id,
            score=score,
            ttc_routes_affected=ttc_routes,
            estimated_commuters=commuters,
            active_permits_overlapping=permits,
            score_breakdown=breakdown
        )

    def _estimate_commuters(self, lat, lng, radius_m) -> int:
        """Count GTFS stop boardings within radius. Uses pre-loaded static data."""
        total = 0
        for stop in GTFS_STOPS:
            if _haversine(lat, lng, stop["lat"], stop["lng"]) <= radius_m:
                total += stop.get("avg_daily_boardings", 500)
        return total

    def _ttc_routes_in_radius(self, lat, lng, radius_m) -> list[str]:
        routes = set()
        for stop in GTFS_STOPS:
            if _haversine(lat, lng, stop["lat"], stop["lng"]) <= radius_m:
                routes.update(stop.get("routes", []))
        return list(routes)

def _haversine(lat1, lng1, lat2, lng2) -> float:
    """Returns distance in meters between two lat/lng points."""
    R = 6371000
    φ1, φ2 = radians(lat1), radians(lat2)
    dφ = radians(lat2 - lat1)
    dλ = radians(lng2 - lng1)
    a = sin(dφ/2)**2 + cos(φ1)*cos(φ2)*sin(dλ/2)**2
    return R * 2 * asin(sqrt(a))
```

### 4.5 Briefing Agent

```python
# agents/briefing_agent.py
"""
Single responsibility: generate plain-language operational brief
and structured dispatch payload.
Tools: lookup_historical_pattern, generate_operational_brief,
       emit_dispatch_payload, fetch_bikeshare_nearby, fetch_parking_nearby.
HITL enforced: dispatch payload requires human_approved=True.
"""
BRIEFING_SYSTEM_PROMPT = """
You are an urban operations briefing writer for the City of Toronto.
You write clear, direct operational briefs that a city supervisor
reads at the start of their shift.

Rules:
- Situation: 1-2 sentences. What is happening RIGHT NOW.
- Root cause: 1 sentence. The likely original trigger.
- Impacts: bullet list. Specific, actionable.
- Action: 1 sentence. Exactly what the supervisor should do first.
- Departments: list only departments that need to act.
- Draft message: a ready-to-send message between departments.

Write for someone with 30 seconds to read this.
No jargon. No hedging. Be direct.

Respond in valid JSON matching the OperationalBrief schema exactly.
"""

class BriefingAgent:
    def generate(
        self,
        cluster: ClusterCandidate,
        correlation: CorrelationResult,
        severity: SeverityScore,
        history: HistoricalMatch
    ) -> OperationalBrief:
        # Only brief high-severity confirmed clusters
        if severity.score < MIN_SEVERITY_FOR_BRIEF:
            return None

        prompt = self._build_prompt(cluster, correlation, severity, history)
        for attempt in range(2):
            try:
                raw = call_nemotron_structured(
                    system=BRIEFING_SYSTEM_PROMPT,
                    user=prompt,
                    max_tokens=400
                )
                data = json.loads(raw)
                brief = OperationalBrief(
                    cluster_id=cluster.cluster_id,
                    severity=severity,
                    historical_match=history,
                    **data
                )
                return brief
            except Exception as e:
                if attempt == 1:
                    return None

    def build_dispatch(
        self,
        brief: OperationalBrief,
        human_approved: bool
    ) -> DispatchPayload:
        """HITL enforced. Raises if human_approved is False."""
        if not human_approved:
            raise HumanApprovalRequired(
                "Dispatch payload requires human approval before emission"
            )

        # Determine primary action from cascade type
        action_map = {
            "watermain_to_road_to_ttc": "suggest_ttc_short_turn",
            "watermain_to_road": "notify_department",
            "road_to_ttc": "suggest_ttc_short_turn",
            "flooding_cascade": "consolidate_311_crew",
            "utility_to_road": "notify_department"
        }

        return DispatchPayload(
            action_type=action_map.get(
                brief.severity.score_breakdown.get("cascade_type_name"),
                "notify_department"
            ),
            priority="critical" if brief.severity.score >= 8
                     else "high" if brief.severity.score >= 6
                     else "medium",
            target_department=brief.departments_to_notify[0],
            payload={
                "cluster_id": brief.cluster_id,
                "message": brief.draft_coordination_message,
                "severity_score": brief.severity.score,
                "estimated_commuters": brief.severity.estimated_commuters,
                "ttc_routes": brief.severity.ttc_routes_affected
            },
            requires_human_approval=True
        )
```

### 4.6 Memory Agent (Overnight)

```python
# agents/memory_agent.py
"""
Single responsibility: review yesterday's cluster outcomes,
detect confirmed cascades, write pattern records.
Runs via cron at 02:00. Never runs during live demo.
Input: cluster_log table from yesterday.
Output: new rows in pattern_memory table.
"""
class MemoryAgent:
    def run_nightly(self):
        """
        Review yesterday's clusters. For each:
        1. Count 311 follow-up calls within 400m and 4 hours
        2. Check if TTC delays appeared on affected routes
        3. Write outcome record to pattern_memory
        4. Increment confidence if pattern repeats
        """
        yesterday_clusters = self._fetch_yesterday_clusters()
        for cluster in yesterday_clusters:
            outcome = self._measure_outcome(cluster)
            self._write_pattern_memory(cluster, outcome)
```

---

## 5. Phase 3 — State Machine Graph
**Duration: Friday night**

```python
# state/graph.py
from langgraph.graph import StateGraph, END
from specs.data_contracts import PipelineState
from agents import (
    ingestion_agent, correlation_agent,
    impact_agent, briefing_agent
)
from config import MIN_SEVERITY_FOR_BRIEF, MIN_CONFIDENCE_THRESHOLD

def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)

    # Register nodes
    graph.add_node("ingest", ingestion_agent.run_cycle)
    graph.add_node("cluster", correlation_agent.cluster)
    graph.add_node("reason", correlation_agent.reason)
    graph.add_node("impact", impact_agent.score_all)
    graph.add_node("brief", briefing_agent.generate_all)
    graph.add_node("hitl", hitl_gate)
    graph.add_node("dispatch", briefing_agent.dispatch_all)

    # Entry point
    graph.set_entry_point("ingest")

    # Deterministic edges
    graph.add_conditional_edges("ingest", route_after_ingest)
    graph.add_conditional_edges("cluster", route_after_cluster)
    graph.add_conditional_edges("reason", route_after_reason)
    graph.add_conditional_edges("impact", route_after_impact)
    graph.add_edge("brief", "hitl")
    graph.add_conditional_edges("hitl", route_after_hitl)
    graph.add_edge("dispatch", END)

    return graph.compile()

# Routing functions — pure, testable, no side effects
def route_after_ingest(state: PipelineState) -> str:
    if state.iteration_count >= 5: return END
    return "cluster" if state.raw_events else END

def route_after_cluster(state: PipelineState) -> str:
    return "reason" if state.cluster_candidates else END

def route_after_reason(state: PipelineState) -> str:
    causal = [r for r in state.correlation_results
              if r.is_causal and r.confidence >= MIN_CONFIDENCE_THRESHOLD]
    return "impact" if causal else END

def route_after_impact(state: PipelineState) -> str:
    high = [s for s in state.severity_scores
            if s.score >= MIN_SEVERITY_FOR_BRIEF]
    return "brief" if high else END

def route_after_hitl(state: PipelineState) -> str:
    return "dispatch" if state.human_approved else END

def hitl_gate(state: PipelineState) -> PipelineState:
    """
    In demo mode: auto-approve after 3 second delay (visible pause).
    In production: wait for supervisor button click in dashboard.
    """
    import time
    time.sleep(3)  # Visible reasoning pause in demo
    state.human_approved = True  # Demo mode auto-approve
    return state
```

---

## 6. Phase 4 — Evals (Before Dashboard)
**Duration: Saturday morning**

### 6.1 Correlation Eval (LLM-as-Judge)

```python
# evals/test_correlation.py
"""
Evaluate CorrelationAgent reasoning quality using mock data only.
Never uses live feeds. Uses a second LLM call as judge.
"""
import pytest
from agents.correlation_agent import CorrelationAgent
from evals.mock_data.fixtures import CAUSAL_CLUSTERS, NON_CAUSAL_CLUSTERS

JUDGE_PROMPT = """
You are evaluating the reasoning quality of an urban operations AI.

Event cluster: {cluster}
AI reasoning: {reasoning}
AI conclusion: is_causal={is_causal}, confidence={confidence}

Score the reasoning 1-5 on:
1. Factual accuracy (does watermain break plausibly cause road closure?)
2. Confidence calibration (is confidence appropriate to the evidence?)
3. Reasoning clarity (is the explanation clear and specific?)

Respond: {"score": int, "critique": "string"}
"""

agent = CorrelationAgent()

@pytest.mark.parametrize("cluster,expected_causal", CAUSAL_CLUSTERS)
def test_causal_clusters_detected(cluster, expected_causal):
    result = agent._reason(cluster, "")
    assert result.is_causal == expected_causal
    assert result.confidence >= 0.6

@pytest.mark.parametrize("cluster", NON_CAUSAL_CLUSTERS)
def test_non_causal_clusters_rejected(cluster):
    result = agent._reason(cluster, "")
    assert not result.is_causal or result.confidence < 0.5

def test_llm_judge_scores_reasoning():
    """LLM-as-judge scoring. Fails if avg score < 3.5/5."""
    scores = []
    for cluster, _ in CAUSAL_CLUSTERS[:5]:
        result = agent._reason(cluster, "")
        judge_response = call_dev_model(
            JUDGE_PROMPT.format(
                cluster=cluster, reasoning=result.reasoning,
                is_causal=result.is_causal, confidence=result.confidence
            )
        )
        scores.append(json.loads(judge_response)["score"])
    assert sum(scores) / len(scores) >= 3.5
```

### 6.2 Adversarial Tests

```python
# evals/test_adversarial.py
"""
Test that agents refuse or handle gracefully on adversarial inputs.
"""

def test_rejects_events_outside_toronto():
    """Events in Mississauga should be dropped at validation."""
    bad_event = {"lat": 43.5890, "lng": -79.6441, ...}  # Mississauga
    result = road_restrictions.to_unified_event(bad_event)
    assert result is None

def test_handles_malformed_json_feed():
    """Feed with invalid escapes should not crash — returns empty list."""
    with patch("requests.get") as mock:
        mock.return_value.text = '{"Closure": [{"id": "bad\\escape"}]}'
        result = road_restrictions.fetch_raw()
        assert isinstance(result, list)

def test_dispatch_requires_human_approval():
    """Dispatch without HITL approval must raise."""
    with pytest.raises(HumanApprovalRequired):
        briefing_agent.build_dispatch(mock_brief, human_approved=False)

def test_circuit_breaker_fires_at_max_iterations():
    """Pipeline must stop at MAX_AGENT_ITERATIONS."""
    # Create a state that always routes back to ingest
    state = PipelineState(...)
    state.iteration_count = 5
    next_node = route_after_ingest(state)
    assert next_node == END

def test_empty_cluster_not_reasoned():
    """Single-event clusters must never reach LLM."""
    single_event = [make_event()]
    clusters = cluster_by_proximity(single_event, 400, 6)
    assert len(clusters) == 0  # Need >= 2 events to form cluster
```

---

## 7. Phase 5 — Dashboard
**Duration: Saturday afternoon**

### 7.1 FastAPI Backend

```python
# dashboard/app.py
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
import asyncio, json

app = FastAPI()
app.mount("/static", StaticFiles(directory="dashboard/frontend/dist"), name="static")

connected_clients: list[WebSocket] = []

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # Keep alive
    except:
        connected_clients.remove(ws)

async def broadcast(event_type: str, data: dict):
    """Push updates to all connected dashboard clients."""
    message = json.dumps({"type": event_type, "data": data})
    for client in connected_clients:
        try:
            await client.send_text(message)
        except:
            pass

# Called by agents to push updates to dashboard
async def push_new_event(event: UnifiedEvent):
    await broadcast("new_event", event.model_dump())

async def push_cluster(cluster: ClusterCandidate, result: CorrelationResult):
    await broadcast("cluster_detected", {
        "cluster": cluster.model_dump(),
        "correlation": result.model_dump()
    })

async def push_brief(brief: OperationalBrief):
    await broadcast("brief_ready", brief.model_dump())

async def push_agent_log(message: str):
    await broadcast("agent_log", {"message": message, "ts": datetime.now().isoformat()})
```

### 7.2 Replay Engine

```python
# dashboard/replay.py
"""
Replay a historical scenario JSON file as if events are arriving live.
Used for demo when live feeds are quiet or unreliable.
Speeds: 1x (real time), 10x, 60x.
"""
import asyncio, json
from pathlib import Path

async def replay_scenario(
    scenario_path: str,
    speed_multiplier: int = 60,
    push_fn = None
):
    """
    Load scenario JSON, replay events in timestamp order.
    Pauses between events scaled by speed_multiplier.
    push_fn: called with each UnifiedEvent as it 'arrives'.
    """
    events = json.loads(Path(scenario_path).read_text())
    events.sort(key=lambda e: e["timestamp"])

    prev_ts = None
    for raw_event in events:
        event = UnifiedEvent(**raw_event)
        if prev_ts:
            gap = (event.timestamp - prev_ts).seconds
            await asyncio.sleep(gap / speed_multiplier)
        if push_fn:
            await push_fn(event)
        prev_ts = event.timestamp
```

---

## 8. Phase 6 — External Integrations
**Duration: Saturday evening (after core works)**

```python
# tools/external_tools.py
import requests

def fetch_bikeshare_nearby(lat: float, lng: float, radius_m: int = 500) -> list[dict]:
    """
    Fetch Bike Share Toronto stations near a location.
    Uses GBFS free public feed. No API key required.
    Returns list of {name, available_bikes, lat, lng, distance_m}.
    """
    try:
        r = requests.get(
            "https://tor.publicbikesystem.net/customer/gbfs/v2/en/station_status",
            timeout=5
        )
        info_r = requests.get(
            "https://tor.publicbikesystem.net/customer/gbfs/v2/en/station_information",
            timeout=5
        )
        stations = {s["station_id"]: s for s in info_r.json()["data"]["stations"]}
        statuses = info_r.json()["data"]["stations"]

        results = []
        for status in r.json()["data"]["stations"]:
            info = stations.get(status["station_id"])
            if not info: continue
            dist = _haversine(lat, lng, info["lat"], info["lon"])
            if dist <= radius_m:
                results.append({
                    "name": info["name"],
                    "available_bikes": status["num_bikes_available"],
                    "lat": info["lat"], "lng": info["lon"],
                    "distance_m": int(dist)
                })
        return sorted(results, key=lambda x: x["distance_m"])[:5]
    except Exception:
        return []

def emit_slack_notification(brief: OperationalBrief, webhook_url: str) -> bool:
    """
    Post operational brief to Slack channel.
    Returns True on success, False on failure — never raises.
    """
    try:
        payload = {
            "text": f"🚨 *StreetSense Alert — Severity {brief.severity.score}/10*",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"*{brief.situation}*"}},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"*Root cause:* {brief.root_cause}"}},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"*Action:* {brief.recommended_action}"}},
                {"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f"Est. {brief.severity.estimated_commuters:,} commuters affected · Routes: {', '.join(brief.severity.ttc_routes_affected)}"}]}
            ]
        }
        r = requests.post(webhook_url, json=payload, timeout=5)
        return r.status_code == 200
    except Exception:
        return False
```

---

## 9. Build Sequence (Weekend Timeline)

```
THURSDAY EVENING (2-3 hrs)
  □ Write specs/data_contracts.py — ALL Pydantic models
  □ Write specs/tool_contracts.md — ALL tool docstrings
  □ Write specs/agent_graph.md — state machine text diagram
  □ Write config.py
  □ Create repo structure, requirements.txt
  □ Build Oct 2024 Bathurst replay scenario JSON file

FRIDAY MORNING (before travel)
  □ ingestion/feeds/road_restrictions.py + test
  □ ingestion/feeds/ttc_alerts.py + test
  □ ingestion/feeds/utility_cuts.py + test
  □ ingestion/geocoder.py + test
  □ ingestion/store.py + init_db()
  □ Run: python -c "from ingestion import *; print('ingestion OK')"

FRIDAY EVENING (on NVIDIA hardware, 4-5 hrs)
  □ Verify Nemotron runs on GB10 via Ollama
  □ Run correlation prompt test against Nemotron
  □ agents/correlation_agent.py
  □ agents/impact_agent.py (load GTFS stops static file)
  □ agents/briefing_agent.py
  □ state/graph.py (LangGraph)
  □ main.py async loop
  □ End-to-end test: replay Bathurst scenario → brief generated

SATURDAY MORNING (3-4 hrs)
  □ evals/test_correlation.py — run and pass
  □ evals/test_adversarial.py — run and pass
  □ Fix any failures found by evals
  □ evals/test_briefing.py — LLM-as-Judge scores >= 3.5

SATURDAY AFTERNOON (4-5 hrs)
  □ dashboard/app.py FastAPI + WebSocket
  □ React frontend: Map.jsx, Sidebar.jsx, AgentLog.jsx
  □ Connect WebSocket to frontend
  □ Test: events appear on map in real time
  □ Replay mode working end-to-end

SATURDAY EVENING (3-4 hrs)
  □ tools/external_tools.py: Bike Share + Slack
  □ Historical heatmap overlay on map
  □ Population metric showing in severity card
  □ agents/memory_agent.py (overnight batch)
  □ Full demo run-through on GB10

SUNDAY MORNING (2 hrs)
  □ Polish: agent reasoning log panel
  □ Prepare 3-screen "silos" opening slide
  □ Rehearse demo narrative 3 times
  □ Submission
```

---

## 10. Demo Script (Exact Sequence)

```
0:00 — Open with 3 screenshots side by side
       "Three real Toronto systems. Same corridor. Same day. Zero communication."

0:30 — Switch to StreetSense live dashboard
       "This is what coordination looks like."
       Point to active Queen St W watermain cluster

1:00 — Agent log panel visible
       "Watch the system reason."
       Read aloud: "Ingesting... 3 events within 380m...
       sending to Nemotron... confidence 0.87..."

1:20 — Brief fires in sidebar
       Read first two sentences
       Slack notification arrives simultaneously

1:40 — Show severity card: "8/10 — est. 14,200 commuters"
       Show historical card: "Similar cluster, Oct 2024, uncoordinated 4 hours"
       Show Bike Share pins on map

2:00 — Dispatch JSON payload visible on screen
       "This is the API contract. Any city system consumes it."

2:20 — Switch to replay mode, Bathurst Oct 2024
       "This is real data. This happened. Nobody caught it."
       Watch cluster form in real time

2:40 — Close
       "Runs entirely on-device. No cloud API.
       Toronto spends $400M a year reacting.
       This is what proactive looks like."
```

---

## 11. Risk Register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Nemotron too slow on GB10 | Medium | Test Friday night. Fallback: qwen2.5:14b |
| Live feeds return malformed data | High | Pydantic catches it. Replay mode as backup |
| LLM returns invalid JSON | Medium | 2 retries + fallback CorrelationResult |
| Geocoding rate limit | Low | Cache pre-populated from 311 data |
| Frontend not ready | Medium | Streamlit fallback (1 hour to build) |
| Demo feeds quiet | Low | Replay mode always available |
| GB10 not available Friday | Low | Full stack runs on Mac M4 during dev |

---

## 12. Definition of Done

- [x] All Pydantic models validated with 10+ test events
- [x] Ingestion agent processes all 4 feeds without crashing
- [x] Correlation agent correctly identifies Oct 2024 Bathurst scenario
- [x] Severity score for that scenario >= 7/10 (gemma4: 7/10 ✓)
- [ ] Brief generated in < 5 seconds on GB10
- [x] Slack notification wired (requires SLACK_WEBHOOK_URL env var)
- [x] Replay mode runs Bathurst scenario end-to-end
- [x] All adversarial tests pass (267/267)
- [x] LLM-as-Judge correlation score >= 7/10 (mistral-nemo judge: 8/10 ✓)
- [x] Dashboard shows events, clusters, brief, and agent log
- [ ] Demo rehearsed 3 times under 3 minutes

---

## 13. Phase 7 — Enhanced Dashboard Features
**Added post-initial-build. All items below are complete or in-progress.**

### 13.1 Economic Cost Counter

A live ticking dollar counter in the dashboard header. Starts when a severity ≥ 4 brief
fires. Based on: `estimated_commuters × $1/hr average delay cost × hours_elapsed`.

```
Formula: cost_per_hour = estimated_commuters × 1.0   (conservative $1/person/hr)
Display: "$14,200 / hr uncoordinated cost" → ticks up every second
Resets: when new pipeline run completes
```

**Files:** `dashboard/templates/index.html` — JS cost counter widget in header.

---

### 13.2 Browser Push Notifications

Web Notifications API — no webhook required. When a severity ≥ 7 brief fires, the
browser pushes a system notification even if the tab is minimised.

```python
# Triggered in JS after renderData()
if (Notification.permission === "granted" && maxSev >= 7) {
    new Notification("StreetSense Alert", {
        body: `SEV ${maxSev}/10 — ${headline} — ${commuters} commuters`,
        icon: "/static/icon.png"
    });
}
```

**Files:** `dashboard/templates/index.html` — JS notification request on load.

---

### 13.3 Auto-Refresh Live Mode

A toggle button that polls `/api/db` every 60 seconds. Shows a countdown timer.
New events animate onto the map when they appear. Makes the "deployed box" story real.

```
State: auto_refresh = false (default)
On enable: setInterval(loadDb, 60000) + countdown display
On new events: diff against previous state, animate new pins
```

**Files:** `dashboard/templates/index.html` — auto-refresh toggle + countdown widget.

---

### 13.4 Incident Timeline Panel

A horizontal timeline in the sidebar showing the cascade from first event to dispatch:

```
08:43 ──●── Watermain break reported (311)
09:00 ──●── Road closure filed (Road Restrictions)
09:15 ──●── 511 diversion alert (TTC)
09:16 ──●── Cluster formed (3 events, 150m radius)
09:17 ──●── LLM: is_causal=True, confidence 0.95
09:18 ──●── Brief generated (SEV 7/10)
09:18 ──●── Dispatch: Toronto Water + TTC Operations
```

Time between first event and brief = "detection-to-brief latency".
Compare to historical: "Without StreetSense, coordination took 4 hours."

**Files:** `dashboard/templates/index.html` — timeline panel below agent log.
`dashboard/app.py` — include event timestamps in API response.

---

### 13.5 TTC Real-Time Vehicle Positions

Plots actual TTC bus and streetcar positions on the Leaflet map as moving markers.
When a cluster fires, you can visually see 511 Bathurst streetcars divert.

```
Feed: https://gtfsrt.ttc.ca/Vehicleposition?format=text
Poll: every 30 seconds
Filter: only routes in affected_routes from the active brief
Display: small directional arrows, colour-coded by route
```

**Files:**
- `ingestion/feeds/ttc_vehicles.py` — fetch + parse vehicle positions
- `tools/db_tools.py` — `fetch_vehicle_positions(routes)` ACI tool
- `dashboard/app.py` — `/api/vehicles` endpoint
- `dashboard/templates/index.html` — rotating marker layer, 30s poll

---

### 13.6 Cascade Prediction

After a cluster fires, analyse which TTC routes pass within 400m of the cluster centroid
that have NOT yet filed a disruption alert. Flag them as "at-risk" in the brief.

```
Algorithm:
  1. For each affected cluster, load TTC route shapes (GTFS static)
  2. Find routes within CLUSTER_RADIUS_M of centroid
  3. Cross-reference against active TTC alert event_ids
  4. Routes present in GTFS but absent from alerts = at-risk
  5. Add to brief: "At-risk routes not yet alerted: [506 College, 510 Spadina]"
```

**Implementation:**
- `tools/gtfs_tools.py` — load GTFS shapes, find routes near point
- `agents/correlation_agent.py` — call `predict_cascade()` after clustering
- `specs/data_contracts.py` — add `at_risk_routes: list[str]` to CorrelationResult
- `dashboard/templates/index.html` — highlight at-risk routes in amber on map

---

## 14. Updated Demo Script (with Phase 7 features)

```
0:00 — Opening slide (3 real Toronto screenshots)
       "Three systems. Same corridor. Same day. Zero communication."

0:20 — Switch to StreetSense dashboard (auto-refresh mode active)
       "This is the deployed box. Running right now. No cloud."
       Point to live event pins on map

0:35 — Trigger Bathurst Oct 2024 replay
       "Let me show you October 2nd, 2024."
       Watch events appear one by one (TTC vehicles visible, diverting)

1:00 — Cluster circle draws. Agent log fires.
       Read aloud: "gemma4: is_causal=True, confidence 0.95"
       Economic cost counter appears: "$14,200/hr uncoordinated"

1:15 — Brief fires. Browser push notification arrives.
       Read headline aloud.
       "This notification arrived 35 minutes after the first report.
        Without StreetSense, coordination took 4 hours."

1:30 — Point to incident timeline panel
       "First event: 08:43. Brief in supervisor's hands: 09:18.
        35 minutes. Not 4 hours."

1:45 — Historical card: "Similar event Oct 2024, uncoordinated 4h"
       Cascade prediction: "At-risk: Route 506 College — no alert filed yet"
       Bike Share pins on map: "Alternatives surfaced automatically"

2:00 — Dispatch payload panel
       "This is the API contract. Any city system consumes it."
       "Toronto Water + TTC Operations. Priority: HIGH."

2:15 — Close
       "Toronto spends $400M reacting to infrastructure failures.
        This is proactive. One box. No cloud. No vendor lock-in."
```

---

## 15. Updated Definition of Done (Phase 7)

- [x] Economic cost counter ticking in dashboard header
- [x] Browser push notifications on severity ≥ 7
- [x] Auto-refresh live mode with 60s countdown
- [x] Incident timeline panel in sidebar
- [ ] TTC vehicle positions on map (requires GTFS-RT vehicle feed)
- [ ] Cascade prediction (requires GTFS static route shapes)
- [ ] Slack webhook live-tested end-to-end
- [ ] Demo rehearsed 3 times under 3 minutes
