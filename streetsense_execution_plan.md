# StreetSense — Execution Plan
> Pre-Event (Now → Thursday) + During Event (Friday → Sunday)
> Hardware: Mac M4 32GB + Ollama qwen2.5:14b until Friday pickup

---

## The Core Strategy

Your Mac M4 with qwen2.5:14b is not a limitation — it is your dev environment.
Everything you build before the event is **model-agnostic by design**.
One environment variable switches between qwen2.5:14b (Mac) and nemotron (GB10).
On Friday night you change one line in config.py and the entire system upgrades.

```python
# config.py — the only line that changes on Friday
MODEL = os.getenv("STREETSENSE_MODEL", "qwen2.5:14b")  # default = Mac dev
# On GB10: export STREETSENSE_MODEL=nemotron-mini
```

---

# PART 1 — PRE-EVENT
### Window: Now → Thursday night
### Machine: Mac M4 + qwen2.5:14b via Ollama
### Goal: Arrive Friday with zero unknowns. Only assembly remains.

---

## Day 1 (Today/Tomorrow) — Specs + Data Layer

**Morning: Write the specs. Nothing else.**

Create the repo and write these three files before touching any implementation:

```
streetsense/
├── specs/
│   ├── data_contracts.py     ← Write this first
│   ├── tool_contracts.md     ← Write this second  
│   └── agent_graph.md        ← Write this third
├── config.py                 ← Write this fourth
└── requirements.txt
```

`requirements.txt` to install now:
```
pydantic>=2.0
requests
certifi
langgraph
langchain-community
ollama
fastapi
uvicorn[standard]
websockets
sqlite3
pytest
python-dotenv
gtfs-realtime-bindings
```

Run `pip install -r requirements.txt` and confirm everything installs cleanly on your Mac.

---

**Afternoon: Data layer — feeds and store**

Build and test each feed file in isolation. Test means: run it, print the output, confirm real data comes back.

**Feed 1 — Road Restrictions (already verified working)**
```python
# Quick smoke test — run this and confirm output
python3 -c "
from ingestion.feeds.road_restrictions import fetch_raw, to_unified_event
records = fetch_raw()
events = [to_unified_event(r) for r in records[:5]]
events = [e for e in events if e]
for e in events:
    print(e.event_type, e.lat, e.lng, e.address[:50])
"
```
Expected: 5 events with real lat/lng printed.

**Feed 2 — TTC Alerts**
```python
# TTC text format parser
import requests, re

def parse_ttc_text(text: str) -> list[dict]:
    """Parse GTFS-RT text format into dicts."""
    alerts = []
    current = {}
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('entity {'):
            current = {}
        elif line.startswith('id:'):
            current['id'] = line.split('"')[1]
        elif 'header_text' in line and 'translation' in line:
            pass
        elif line.startswith('text:') and 'header' in str(current):
            current['text'] = line.split('"')[1] if '"' in line else ''
        elif line == '}' and current.get('id'):
            alerts.append(current)
            current = {}
    return alerts

# Test it
r = requests.get("https://gtfsrt.ttc.ca/alerts/all?format=text")
alerts = parse_ttc_text(r.text)
print(f"TTC alerts: {len(alerts)}")
for a in alerts[:3]:
    print(a)
```

**Feed 3 — Utility Cuts (already verified working)**
```python
python3 -c "
import requests
r = requests.get('https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/43cbc364-b673-49ca-b98b-8b99c5d5f6eb/resource/3bf43fcc-6c50-441c-862e-afbdb31d9a53/download/utility-cut-permits-data.json')
data = r.json()
print(f'Utility cuts: {len(data)} records')
print(data[0].keys())
print(data[0])
"
```

**SQLite store:**
```python
# Test store init and write
python3 -c "
from ingestion.store import init_db, write_events
init_db()
print('DB initialized OK')
"
```

---

**Evening: Geocoder + normalization**

Build `ingestion/geocoder.py` with Nominatim cache.
Build `ingestion/normalizer.py` — one function per feed that converts raw → UnifiedEvent.

**Critical test to run tonight:**
```python
# Test the full ingestion path end-to-end
python3 -c "
from ingestion.feeds.road_restrictions import fetch_raw, to_unified_event
from ingestion.store import init_db, write_events

init_db()
records = fetch_raw()
events = [to_unified_event(r) for r in records if r]
events = [e for e in events if e is not None]
result = write_events(events)
print(f'Written: {result.success_count}, Failed: {result.failure_count}')
print(f'Errors: {result.errors[:3]}')
"
```

**Definition of done for Day 1:**
- [ ] All 3 feed files written and tested
- [ ] SQLite DB initializes without error
- [ ] Road restrictions: 10+ events written to DB
- [ ] TTC alerts: parsing returns structured dicts
- [ ] Geocoder caches and returns lat/lng for a test address

---

## Day 2 — LLM Integration + Correlation Agent

**This is where qwen2.5:14b does real work.**

### Morning: Lock your prompts against qwen first

The most important thing to do on your Mac is **find prompts that work reliably**.
A prompt that works on qwen2.5:14b will work on Nemotron — Nemotron is stronger,
so if it passes qwen it will pass Nemotron. The reverse is not guaranteed.

**Step 1: Test raw Ollama connection**
```python
import ollama

response = ollama.chat(
    model="qwen2.5:14b",
    messages=[{"role": "user", "content": "Reply with: {\"status\": \"ok\"}"}]
)
print(response['message']['content'])
# Must print clean JSON, nothing else
```

**Step 2: Test correlation prompt with real data**

Pull 3 real events from your DB that look like a cascade, then run:
```python
import ollama, json

# Use your real Oct 2024 Bathurst events
test_cluster = """
Cluster of 3 events within 350m over 2 hours in Toronto:
- [watermain_break] Possible watermain break at Bathurst St & Prue Ave (06:55)
- [watermain_break] Possible watermain break at Bathurst St & Viewmount Ave (07:36)  
- [road_closure] Emergency road closure at Bathurst St & Prue Ave (07:57)

Are these causally related? Respond in JSON only:
{"is_causal": bool, "confidence": float 0-1, 
 "cascade_type": "watermain_to_road|road_to_ttc|watermain_to_road_to_ttc|utility_to_road|flooding_cascade|unrelated",
 "reasoning": "under 200 chars"}
"""

response = ollama.chat(
    model="qwen2.5:14b",
    messages=[
        {"role": "system", "content": "You are an urban operations analyst. Respond in valid JSON only. No preamble."},
        {"role": "user", "content": test_cluster}
    ]
)
raw = response['message']['content']
print("Raw:", raw)

# Must parse cleanly
parsed = json.loads(raw)
print("Parsed:", parsed)
assert parsed['is_causal'] == True
assert parsed['confidence'] > 0.6
print("PASS")
```

**If this fails (returns text before JSON, or invalid JSON):**
Add this to your system prompt: `"You must respond with a JSON object only. Your entire response must start with { and end with }. No other text."`

Keep iterating until this test passes 5 times in a row. That prompt is now locked.

**Step 3: Test briefing prompt**
```python
brief_test = """
Generate an operational brief for this confirmed cascade:

Cascade type: watermain_to_road_to_ttc
Severity: 8/10
Events: Watermain break on Bathurst St, road closure within 400m,
        TTC Route 511 Bathurst streetcar running through affected corridor.
Est. commuters affected: 14,200
Historical match: Similar event Oct 2, 2024 went uncoordinated for 4 hours.

Respond in JSON:
{
  "situation": "1-2 sentences",
  "root_cause": "1 sentence", 
  "downstream_impacts": ["item1", "item2"],
  "recommended_action": "1 sentence",
  "departments_to_notify": ["dept1"],
  "draft_coordination_message": "ready-to-send message"
}
"""

response = ollama.chat(
    model="qwen2.5:14b",
    messages=[
        {"role": "system", "content": "You are an urban operations briefing writer. Respond in valid JSON only."},
        {"role": "user", "content": brief_test}
    ]
)
print(json.loads(response['message']['content']))
```

**Save every passing prompt to `specs/prompts.py`. These are gold.**

---

### Afternoon: Build correlation agent

```python
# agents/correlation_agent.py
# Uses config.MODEL — works with qwen locally, nemotron on GB10
import ollama, json
from config import MODEL
from specs.data_contracts import ClusterCandidate, CorrelationResult
from specs.prompts import CORRELATION_SYSTEM_PROMPT

class CorrelationAgent:
    def reason(self, cluster: ClusterCandidate) -> CorrelationResult:
        prompt = self._build_prompt(cluster)
        for attempt in range(2):
            try:
                response = ollama.chat(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": CORRELATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ]
                )
                raw = response['message']['content'].strip()
                # Strip any preamble if model adds it
                if '{' in raw:
                    raw = raw[raw.index('{'):raw.rindex('}')+1]
                data = json.loads(raw)
                return CorrelationResult(cluster_id=cluster.cluster_id, **data)
            except Exception as e:
                if attempt == 1:
                    return CorrelationResult(
                        cluster_id=cluster.cluster_id,
                        is_causal=False, confidence=0.0,
                        cascade_type="unrelated",
                        reasoning=f"Parse failure: {str(e)[:100]}"
                    )
```

**Test the correlation agent end-to-end on your Mac:**
```python
python3 -c "
from agents.correlation_agent import CorrelationAgent
from tests.fixtures import make_bathurst_cluster

agent = CorrelationAgent()
cluster = make_bathurst_cluster()  # Your Oct 2024 data
result = agent.reason(cluster)
print(f'is_causal: {result.is_causal}')
print(f'confidence: {result.confidence}')
print(f'cascade_type: {result.cascade_type}')
print(f'reasoning: {result.reasoning}')
assert result.is_causal
assert result.confidence > 0.6
print('CORRELATION AGENT: PASS')
"
```

---

### Evening: Impact agent + severity scoring

```python
# No LLM needed here — pure arithmetic
python3 -c "
from agents.impact_agent import ImpactAgent
from tests.fixtures import make_bathurst_correlation, make_bathurst_cluster

agent = ImpactAgent()
score = agent.score(make_bathurst_correlation(), make_bathurst_cluster())
print(f'Severity: {score.score}/10')
print(f'TTC routes: {score.ttc_routes_affected}')
print(f'Commuters: {score.estimated_commuters}')
assert score.score >= 6, f'Expected >= 6, got {score.score}'
print('IMPACT AGENT: PASS')
"
```

Download these static files now (needed for impact scoring):
- Toronto GTFS: `https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=ttc-routes-and-schedules`
- Ward profiles: `https://open.toronto.ca/dataset/ward-profiles-25-ward-model/`

**Definition of done for Day 2:**
- [ ] Correlation prompt works reliably 5/5 times on qwen2.5:14b
- [ ] Briefing prompt works reliably 5/5 times on qwen2.5:14b
- [ ] Both prompts saved to specs/prompts.py
- [ ] CorrelationAgent.reason() passes Bathurst cluster test
- [ ] ImpactAgent.score() returns >= 6/10 for Bathurst cluster
- [ ] GTFS stops file downloaded and parsed

---

## Day 3 — Briefing Agent + State Graph + Evals

### Morning: Briefing agent + full pipeline

```python
# Test full pipeline on your Mac with qwen
python3 -c "
from state.graph import build_graph
from dashboard.replay import load_scenario

graph = build_graph()
events = load_scenario('evals/mock_data/oct2024_bathurst.json')

# Feed first 5 events into graph
from specs.data_contracts import PipelineState
from datetime import datetime
import uuid

state = PipelineState(
    run_id=str(uuid.uuid4()),
    triggered_at=datetime.now(),
    raw_events=events[:5]
)

result = graph.invoke(state)
print(f'Briefs generated: {len(result[\"briefs\"])}')
print(f'Errors: {result[\"errors\"]}')
if result['briefs']:
    print(result['briefs'][0]['situation'])
print('FULL PIPELINE: PASS' if result['briefs'] else 'FULL PIPELINE: NO BRIEFS')
"
```

### Afternoon: Build replay scenario file

This is your most important pre-event asset. Build it now.

```python
# Build oct2024_bathurst.json from your 311 data
python3 << 'EOF'
import json
from datetime import datetime

# Based on what you found in the 311 data mining
scenario = [
    {
        "event_id": "311-oct02-001",
        "source": "311_request",
        "event_type": "watermain_break",
        "lat": 43.7035, "lng": -79.4260,
        "address": "Bathurst St & Prue Ave",
        "timestamp": "2024-10-02T06:55:00",
        "status": "open",
        "description": "Watermain-Possible Break reported at Bathurst & Prue",
        "raw": {"Service Request Type": "Watermain-Possible Break"}
    },
    {
        "event_id": "311-oct02-002",
        "source": "311_request",
        "event_type": "watermain_break",
        "lat": 43.7045, "lng": -79.4262,
        "address": "Bathurst St & Viewmount Ave",
        "timestamp": "2024-10-02T07:36:00",
        "status": "open",
        "description": "Watermain-Possible Break at Bathurst & Viewmount",
        "raw": {"Service Request Type": "Watermain-Possible Break"}
    },
    {
        "event_id": "311-oct02-003",
        "source": "311_request",
        "event_type": "watermain_break",
        "lat": 43.7038, "lng": -79.4261,
        "address": "Bathurst St & Prue Ave",
        "timestamp": "2024-10-02T07:57:00",
        "status": "open",
        "description": "Third report - water main break confirmed area Bathurst & Prue",
        "raw": {"Service Request Type": "Watermain-Possible Break"}
    },
    {
        "event_id": "rr-oct02-001",
        "source": "road_restriction",
        "event_type": "road_closure",
        "lat": 43.7039, "lng": -79.4260,
        "address": "Bathurst St between Prue Ave and Viewmount Ave",
        "timestamp": "2024-10-02T08:30:00",
        "status": "open",
        "description": "Emergency road closure - watermain repair in progress",
        "raw": {"workEventType": "Toronto Water", "contractor": "Emergency Crew"}
    },
    {
        "event_id": "ttc-oct02-001",
        "source": "ttc_alert",
        "event_type": "ttc_disruption",
        "lat": 43.7041, "lng": -79.4258,
        "address": "Bathurst St (511 Streetcar route)",
        "timestamp": "2024-10-02T08:45:00",
        "status": "open",
        "description": "Route 511 Bathurst: Detour in effect due to road closure at Bathurst & Prue",
        "raw": {"route_id": "511", "alert_type": "Detour"}
    },
    {
        "event_id": "311-oct02-004",
        "source": "311_request",
        "event_type": "road_closure",
        "lat": 43.7040, "lng": -79.4259,
        "address": "Bathurst St & Almore Ave",
        "timestamp": "2024-10-02T15:44:00",
        "status": "open",
        "description": "Roadway Utility Cut - Settlement at Bathurst & Almore",
        "raw": {"Service Request Type": "Roadway Utility Cut - Settlement"}
    }
]

with open('evals/mock_data/oct2024_bathurst.json', 'w') as f:
    json.dump(scenario, f, indent=2)

print(f"Scenario written: {len(scenario)} events")
print("Timeline:")
for e in scenario:
    print(f"  {e['timestamp'][11:16]} | {e['event_type']} | {e['address']}")
EOF
```

Also build a second scenario from the current Queen St W active data you found:
```python
# queen_st_active.json — based on live data from today
# Same structure, uses the watermain replacement + 501 streetcar
```

### Evening: Evals

```python
# Run your full eval suite on qwen before event
python3 -m pytest evals/ -v

# Expected output:
# test_causal_clusters_detected PASSED
# test_non_causal_clusters_rejected PASSED  
# test_rejects_events_outside_toronto PASSED
# test_handles_malformed_json_feed PASSED
# test_dispatch_requires_human_approval PASSED
# test_circuit_breaker_fires_at_max_iterations PASSED
# test_empty_cluster_not_reasoned PASSED
```

If any test fails, fix it now. Do not arrive at the event with failing tests.

**Definition of done for Day 3:**
- [ ] Full pipeline runs end-to-end on Mac with qwen2.5:14b
- [ ] oct2024_bathurst.json scenario built and tested
- [ ] queen_st_active.json scenario built and tested
- [ ] All eval tests passing
- [ ] Brief generated in < 8 seconds on Mac (will be faster on GB10)
- [ ] GTFS stops integrated into impact scoring

---

## Day 4 (Thursday) — Dashboard + Final Polish

### Morning: FastAPI + WebSocket backend

```python
# dashboard/app.py
# Build and test locally — serves the React frontend

# Test WebSocket connection
python3 -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://localhost:8000/ws') as ws:
        # Should receive event within 5 seconds when replay runs
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        data = json.loads(msg)
        print('WebSocket OK:', data['type'])

asyncio.run(test())
"
```

### Afternoon: React frontend

Key components to have working before event:
- Map with event pins (Mapbox GL or Leaflet)
- WebSocket connection receiving events and plotting them
- Sidebar showing brief text
- Agent log panel showing reasoning steps
- Replay button that triggers the Bathurst scenario

**Minimum viable frontend test:**
```bash
# Start backend
uvicorn dashboard.app:app --reload &

# Start replay in another terminal
python3 -c "
import asyncio
from dashboard.replay import replay_scenario
asyncio.run(replay_scenario('evals/mock_data/oct2024_bathurst.json', speed_multiplier=10))
"

# Open browser at localhost:3000
# Confirm: events appear on map, brief appears in sidebar
```

### Evening: Integration + final checks

Run the complete demo sequence once on your Mac:

```bash
# Terminal 1: Start the full system
python3 main.py

# Terminal 2: Run Bathurst replay
python3 -c "
from dashboard.replay import trigger_replay
trigger_replay('oct2024_bathurst')
"

# Verify in browser:
# 1. Events appear on map as they arrive
# 2. Agent log shows reasoning steps
# 3. Cluster circle draws around Bathurst events
# 4. Brief appears in sidebar
# 5. Severity score shows 7+/10
# 6. Historical card shows (if pattern_memory has data)
```

**Pack list for the event:**
- [ ] Laptop + charger
- [ ] Mac Mini (backup inference machine if GB10 has issues)
- [ ] USB-C hub
- [ ] `config.py` ready to switch MODEL with one env var
- [ ] Both scenario JSON files in repo
- [ ] All prompts saved in specs/prompts.py
- [ ] `requirements.txt` tested and clean
- [ ] `.env.example` with STREETSENSE_MODEL, SLACK_WEBHOOK_URL
- [ ] Slack workspace created, #toronto-ops channel ready
- [ ] Mapbox API key (free tier, get now at mapbox.com)

---

# PART 2 — DURING THE EVENT
### Window: Friday 6pm → Sunday 9am
### Machine: NVIDIA GB10 Grace Blackwell + Nemotron
### Goal: Port, test on hardware, polish, win.

---

## Friday Night (6pm → 2am) — Hardware Onboarding

### 6:00–7:00pm: GB10 Setup

First thing when you get the hardware, before anything else:

```bash
# 1. Check Ollama is installed or install it
ollama --version || curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull Nemotron
ollama pull nemotron-mini
# While it downloads, set up your repo

# 3. Clone your repo
git clone https://github.com/yourname/streetsense
cd streetsense
pip install -r requirements.txt

# 4. Test Nemotron responds
ollama run nemotron-mini "Reply with only valid JSON: {\"status\": \"ok\"}"
# Must return clean JSON
```

### 7:00–8:00pm: Model Validation

**This hour is critical. Do not skip it.**

Run your locked prompts against Nemotron and confirm they still pass:

```bash
export STREETSENSE_MODEL=nemotron-mini

python3 -c "
from agents.correlation_agent import CorrelationAgent
from tests.fixtures import make_bathurst_cluster

agent = CorrelationAgent()
result = agent.reason(make_bathurst_cluster())
print(f'Model: nemotron-mini')
print(f'is_causal: {result.is_causal}')
print(f'confidence: {result.confidence}')
print(f'cascade_type: {result.cascade_type}')
assert result.is_causal, 'FAIL: Nemotron did not detect causal relationship'
assert result.confidence > 0.6, f'FAIL: confidence too low: {result.confidence}'
print('NEMOTRON CORRELATION: PASS')
"
```

If Nemotron returns different JSON structure than qwen:
- Add output format examples to the system prompt
- Nemotron is instruction-tuned — it may need slightly different phrasing
- Fix the prompt in specs/prompts.py and re-test

```bash
# Measure inference speed on GB10
time python3 -c "
from agents.correlation_agent import CorrelationAgent
from tests.fixtures import make_bathurst_cluster
agent = CorrelationAgent()
result = agent.reason(make_bathurst_cluster())
print(result.cascade_type)
"
# Target: < 3 seconds. GB10 should be well under this.
```

### 8:00–10:00pm: Full Pipeline on GB10

```bash
# Run the complete Bathurst replay on new hardware
export STREETSENSE_MODEL=nemotron-mini
python3 main.py &

# In another terminal
python3 -c "
from dashboard.replay import trigger_replay
trigger_replay('oct2024_bathurst')
"

# Checklist:
# [ ] All 6 events ingest without errors
# [ ] Cluster forms around 3 watermain events
# [ ] Nemotron returns is_causal=True, confidence > 0.6
# [ ] Severity score >= 7/10
# [ ] Brief generated and readable
# [ ] Dashboard updates in real time
```

**If pipeline passes: you are ahead of 90% of teams at this point.**

### 10:00pm–2:00am: Polish + Winning Features

Now and only now, add the winning features. Do them in this order —
stop when you run out of time, earlier items have higher judge impact:

**Priority 1: Agent reasoning log panel (2 hours)**
If not built pre-event, build now. This is visible AI reasoning.
```python
# Add to each agent — emit log messages via WebSocket
await push_agent_log(f"Ingested {len(events)} events from {feed_name}")
await push_agent_log(f"Clustering {len(events)} events within {CLUSTER_RADIUS_M}m...")
await push_agent_log(f"Sending cluster to Nemotron... ({len(cluster.events)} events)")
await push_agent_log(f"Nemotron: is_causal={result.is_causal}, confidence={result.confidence:.2f}")
await push_agent_log(f"Severity: {score.score}/10 — {score.estimated_commuters:,} commuters affected")
await push_agent_log(f"Generating brief...")
```

**Priority 2: Slack notification (30 minutes)**
```python
# If webhook not set up, do it now
# Create Slack workspace → Create app → Incoming Webhooks → Copy URL
# export SLACK_WEBHOOK_URL=https://hooks.slack.com/...
from tools.external_tools import emit_slack_notification
```

**Priority 3: Historical heatmap overlay (1 hour)**
```python
# When cluster matches pattern_memory, render translucent circle on map
# Frontend: add a semi-transparent Mapbox circle layer
# Color: amber for moderate, red for high-confidence match
```

**Priority 4: Bike Share layer (1 hour)**
```python
# GBFS endpoint is free and real-time
# Add pins to map when a cluster fires, showing nearby availability
```

**Stop at 2am regardless of what's done. Sleep matters.**

---

## Saturday (9am → 10pm) — Build Day

### 9:00–11:00am: Morning checks + fixes

```bash
# Run full eval suite on GB10 with Nemotron
export STREETSENSE_MODEL=nemotron-mini
python3 -m pytest evals/ -v

# Fix any failures before building anything new
```

Check live feeds are working:
```bash
python3 -c "
from ingestion.feeds.road_restrictions import fetch_raw
records = fetch_raw()
print(f'Road restrictions live: {len(records)} records')

import requests
r = requests.get('https://gtfsrt.ttc.ca/alerts/all?format=text')
print(f'TTC alerts live: {len(r.text)} chars')
"
```

### 11:00am–2:00pm: Remaining winning features

In priority order, whatever wasn't finished Friday night:
- Population impact metric in severity card
- Dispatch JSON payload display
- Memory agent overnight batch (test manually)
- Green P parking integration

### 2:00–6:00pm: Demo preparation

**Build the opening slide (30 mins)**
Three screenshots side by side:
- Screenshot 1: Toronto 311 dashboard showing water complaints
- Screenshot 2: Road restrictions map showing Queen St closure
- Screenshot 3: TTC service alerts showing Route 501

Take real screenshots from the actual Toronto websites. This makes the problem real.

**Full demo run-through (repeat 3 times)**
```
Time yourself. Must be under 3 minutes.
Run 1: Follow the script exactly
Run 2: Improvise based on what's actually on screen (live feeds)
Run 3: Something goes wrong — practice the recovery
```

**Prepare your "something went wrong" recovery:**
```bash
# If live feeds are quiet or broken during demo:
# Switch to replay mode immediately — do not apologize
python3 -c "from dashboard.replay import trigger_replay; trigger_replay('oct2024_bathurst')"
# Say: "Let me show you a real historical scenario from October 2024"
# This sounds intentional, not like a backup
```

### 6:00–10:00pm: Final polish

- Fix any UI issues from demo run-throughs
- Ensure replay mode is on a keyboard shortcut
- Test Slack notification fires reliably
- Clean up any console errors in browser
- Make sure agent log panel is prominent and readable from 3 meters

---

## Sunday (8:00am → 9:00am) — Submission Morning

```bash
# 8:00am — final system check
export STREETSENSE_MODEL=nemotron-mini
python3 main.py &
sleep 5
python3 -c "from dashboard.replay import trigger_replay; trigger_replay('oct2024_bathurst')"
# Confirm brief appears in dashboard

# 8:30am — do not add new features
# Last 30 minutes: rehearse your opening 30 seconds one more time
# The first 30 seconds determines if judges lean in or zone out
```

---

## The Model Handoff — Exact Steps

```
Mac M4 (qwen2.5:14b)           GB10 (nemotron-mini)
─────────────────────           ─────────────────────
All development                 Port + validate
All prompt engineering          Re-test all prompts
All eval suite passes           Confirm evals still pass
All feed integration            Measure inference speed
Full pipeline working           Full pipeline on new hw
Both scenarios built            Live feeds connected
Dashboard basic working         Dashboard polished
                                Winning features added
                                Demo rehearsed
```

**The only things that change when you switch hardware:**
1. `export STREETSENSE_MODEL=nemotron-mini`
2. Confirm Ollama is running on GB10
3. Run validation suite

Everything else is identical.

---

## Confidence Checkpoints

**After Day 1 (data layer):** If feeds return data and DB writes work, you have the hardest infrastructure problem solved. Most teams fail here.

**After Day 2 (prompts locked):** If qwen2.5:14b correctly identifies the Bathurst cascade 5/5 times, your core AI logic is solid. Nemotron will be better.

**After Day 3 (pipeline + evals):** If the full pipeline runs end-to-end on your Mac and all evals pass, you are ready for the event. Everything after this is polish.

**After Friday 10pm (GB10 pipeline passes):** If the replay scenario produces a brief on Nemotron, you have a winning product. Everything after this makes it more winning.

**Sunday 8am:** If the Bathurst replay produces a brief with severity 7+, Slack fires, and the dashboard updates — you win. Anything else is a bonus.

---

## What "Done Enough to Win" Looks Like

Minimum:
- Live map with event pins
- Cluster detection fires on replay scenario
- Brief appears in sidebar with severity score
- Agent reasoning log is visible
- Runs on GB10 locally (no cloud)

Winning:
- All of the above PLUS
- Historical match card with Oct 2024 data
- Slack notification fires in real time
- Population impact number in severity card
- Bike Share alternatives on map
- Dispatch JSON payload visible

Dominating:
- All of the above PLUS
- Live feeds running alongside replay
- Overnight pattern learning demo
- Green P parking layer
- Opening "three silos" slide lands the problem viscerally
