# StreetSense — Hackathon Weekend Plan
> Friday Evening → Sunday Morning Submission
> Spec-driven with unit tests at every stage

---

## What "Pre-Event Complete" Means

Before you walk in Friday, ALL of these are true on your Mac:

**Code:**
- [ ] All 4 feed files written and tested
- [ ] Pydantic models written and all contract tests pass
- [ ] SQLite store written and all store tests pass
- [ ] Geocoder written and all geocoder tests pass
- [ ] Correlation agent written and all unit tests pass
- [ ] Briefing agent written and all unit tests pass
- [ ] LangGraph graph written and integration test passes
- [ ] Dashboard backend written and basic test passes
- [ ] Replay mode written and tested

**Tests:**
- [ ] `python3 -m pytest tests/ -v` → all green
- [ ] `python3 -m pytest evals/test_adversarial.py -v` → all green
- [ ] Eval tests run manually on qwen2.5:14b → LLM-as-Judge scores ≥ 3.5/5

**Demo assets:**
- [ ] oct2024_bathurst.json scenario file built and replay tested
- [ ] queen_st_active.json scenario file built and replay tested
- [ ] Slack workspace created, #toronto-ops channel ready, webhook URL in .env
- [ ] Mapbox API token in .env
- [ ] Opening three-screenshot slide saved as image file

If any item is not checked, do it before touching the GB10.
The GB10 runs the same code faster — it is not a place to finish building.

---

# FRIDAY EVENING
## 6:00pm → 2:00am (8 hours)
### Goal: Full test suite green on NVIDIA hardware by midnight

---

### 6:00 – 6:30pm | Arrive and Orient

Do not open your laptop yet:
- Find your table, plug in, get water
- Introduce yourself to neighbors
- Find the wifi password, confirm internet works
- Ask organizers: when do you get the GB10, what OS is on it

When you have the GB10, open a terminal and run:
```bash
ollama --version && python3 --version && git --version && pytest --version
```
Paste the output. If anything is missing, install it before anything else.

---

### 6:30 – 7:30pm | GB10 Environment Setup

```bash
# Step 1 — Pull Nemotron first (takes time, do it immediately)
ollama pull nemotron-mini

# Step 2 — While it downloads, clone your repo
git clone https://github.com/YOUR_USERNAME/streetsense
cd streetsense

# Step 3 — Install dependencies
pip install -r requirements.txt

# Step 4 — Create .env file
cat > .env << EOF
STREETSENSE_MODEL=nemotron-mini
SLACK_WEBHOOK_URL=your_webhook_url_here
MAPBOX_TOKEN=your_mapbox_token_here
DB_PATH=streetsense.db
EOF

# Step 5 — Initialize the database
python3 -c "from ingestion.store import init_db; init_db(); print('DB OK')"

# Step 6 — Confirm Nemotron downloaded
ollama list
```

Expected output of `ollama list`: shows nemotron-mini with a size.
If it is still downloading, wait. Do not continue until it is ready.

---

### 7:30 – 8:30pm | Run Full Test Suite on GB10 (Most Important Hour)

**This is the most important hour of the entire event.**

The goal: confirm every test that passed on your Mac also passes on the GB10.
Unit tests should pass immediately — they use mock data, no LLM involved.
Eval tests need Nemotron running.

**Step 1 — Run all unit and integration tests:**
```bash
export STREETSENSE_MODEL=nemotron-mini
python3 -m pytest tests/ -v --tb=short 2>&1 | tee test_results_friday.txt
```

Paste the last 30 lines. Expected: all green.

If any unit test fails on GB10 but passed on Mac:
- It is almost certainly a Python version or package version difference
- Read the error message carefully — it will say exactly what is wrong
- Fix it before running eval tests

**Step 2 — Run adversarial tests:**
```bash
python3 -m pytest evals/test_adversarial.py -v --tb=short
```

Paste the output. Expected: all green.

**Step 3 — Run correlation eval test with Nemotron:**
```bash
python3 -m pytest evals/test_correlation_quality.py -v --tb=short -s
```

This calls Nemotron and scores the reasoning quality.
Expected: LLM-as-Judge average score ≥ 3.5/5, all causal clusters detected.

Paste the output. If Nemotron returns different JSON structure than qwen:
```python
# Fix in specs/prompts.py — add this to CORRELATION_SYSTEM_PROMPT:
"Your entire response must be a single JSON object. 
Start your response with { and end with }. 
Do not include any text before or after the JSON object."
```
Re-run the eval test until it passes.

**Step 4 — Measure Nemotron inference speed:**
```bash
time python3 -m pytest evals/test_correlation_quality.py::test_single_inference_speed -v -s
```

Expected: single LLM call under 3 seconds on GB10.
If over 5 seconds: the model is not using the GPU properly. Check:
```bash
# Watch GPU utilization while inference runs
# In a second terminal:
nvidia-smi -l 1
```

**All 4 steps green = you are ahead of 90% of teams at 8:30pm Friday.**

---

### 8:30 – 10:00pm | Full Pipeline Validation

Run the complete end-to-end pipeline on new hardware using your demo scenario.

**Terminal 1 — Start the system:**
```bash
export STREETSENSE_MODEL=nemotron-mini
python3 main.py
```

**Terminal 2 — Run the integration test:**
```bash
python3 -m pytest tests/integration/test_replay_scenario.py -v -s
```

This test:
1. Loads oct2024_bathurst.json
2. Feeds all 6 events into the pipeline
3. Asserts a cluster is detected
4. Asserts CorrelationResult.is_causal == True
5. Asserts SeverityScore.score >= 7
6. Asserts an OperationalBrief is generated
7. Asserts brief.situation is non-empty
8. Asserts DispatchPayload is emitted

Paste the output. Every assertion must pass.

**Also verify in the browser at localhost:3000:**
- [ ] Events appear on map as they arrive
- [ ] Agent log shows reasoning steps
- [ ] Cluster circle draws around Bathurst events
- [ ] Brief appears in sidebar
- [ ] Severity shows 7+ / 10
- [ ] Slack notification arrives in #toronto-ops

**If the integration test passes and all 6 browser checkboxes are checked:**
You have a working product. It is 10pm Friday.
Everything from here makes it more winning, not more working.

---

### 10:00pm – 2:00am | Winning Features

Add these in strict priority order. Stop when you run out of time.
After adding each feature, run its test before moving to the next.

**Priority 1 — Agent reasoning log panel (1 hour)**

Add log emission to each agent, write the test, verify it works:
```python
# Test to write alongside the feature:
# tests/agents/test_agent_logging.py

def test_correlation_agent_emits_log_messages(mock_websocket, bathurst_cluster):
    """Correlation agent must emit at minimum 3 log messages per run."""
    agent = CorrelationAgent(log_fn=mock_websocket.send)
    agent.reason(bathurst_cluster)
    assert mock_websocket.send.call_count >= 3
    messages = [call.args[0] for call in mock_websocket.send.call_args_list]
    assert any("Clustering" in m for m in messages)
    assert any("Nemotron" in m or "confidence" in m.lower() for m in messages)
    assert any("Severity" in m or "severity" in m for m in messages)
```

Run test, confirm it passes, then verify visually in the browser.

**Priority 2 — Slack notification (30 minutes)**

Should already be built from pre-event. Confirm it fires:
```bash
python3 -m pytest tests/tools/test_external_tools.py::test_slack_notification_sends -v -s
```

If the test passes but Slack is not receiving:
- Check your SLACK_WEBHOOK_URL in .env
- Run: `python3 -c "from tools.external_tools import emit_slack_notification; print(emit_slack_notification(None, dry_run=True))"`

**Priority 3 — Historical match card (45 minutes)**

The sidebar card showing "Similar event Oct 2024, uncoordinated 4 hours."
Test it:
```python
# tests/agents/test_briefing_agent.py — add this test

def test_brief_includes_historical_match_when_available(
    bathurst_cluster, causal_correlation, bathurst_severity, historical_match
):
    """When pattern_memory has a match, brief must include it."""
    agent = BriefingAgent()
    brief = agent.generate(
        bathurst_cluster, causal_correlation, bathurst_severity, historical_match
    )
    assert brief.historical_match is not None
    assert brief.historical_match.match_found == True
    assert brief.historical_match.uncoordinated_hours == 4.0

def test_brief_handles_no_historical_match_gracefully(
    bathurst_cluster, causal_correlation, bathurst_severity
):
    """When no pattern exists, brief must still generate without error."""
    agent = BriefingAgent()
    brief = agent.generate(
        bathurst_cluster, causal_correlation, bathurst_severity, None
    )
    assert brief is not None
    assert brief.situation != ""
```

**Priority 4 — Population impact number (30 minutes)**

The "Est. 14,200 peak-hour commuters affected" number in the severity card.
Test it:
```python
# tests/agents/test_impact_agent.py — add this test

def test_commuter_estimate_is_nonzero_for_bathurst(bathurst_cluster, causal_correlation):
    """Bathurst corridor has TTC stops — commuter estimate must be > 0."""
    agent = ImpactAgent()
    score = agent.score(causal_correlation, bathurst_cluster)
    assert score.estimated_commuters > 0
    assert len(score.ttc_routes_affected) > 0
    # 511 Bathurst must be in affected routes
    assert any("511" in r for r in score.ttc_routes_affected)
```

**Priority 5 — Bike Share layer (45 minutes)**

```python
# tests/tools/test_external_tools.py — add this test

def test_bikeshare_returns_stations_near_bathurst():
    """Should find Bike Share stations near Bathurst & King."""
    stations = fetch_bikeshare_nearby(lat=43.7035, lng=-79.4260, radius_m=800)
    # May be empty if no stations nearby — that is OK
    # But must not raise an exception
    assert isinstance(stations, list)
    for s in stations:
        assert "name" in s
        assert "bikes" in s
        assert s["distance_m"] <= 800

def test_bikeshare_handles_network_failure_gracefully():
    """Network failure must return empty list, not raise exception."""
    with patch("requests.get", side_effect=ConnectionError("network down")):
        result = fetch_bikeshare_nearby(43.7035, -79.4260)
    assert result == []
```

**Stop at 2am. Sleep is not optional.**

---

# SATURDAY
## 9:00am → 10:00pm (13 hours)
### Goal: All features tested and working, demo rehearsed

---

### 9:00 – 9:30am | Morning Health Check

Before anything else, run the full test suite:
```bash
export STREETSENSE_MODEL=nemotron-mini
python3 -m pytest tests/ evals/test_adversarial.py -v --tb=short 2>&1 | tail -40
```

Paste the output. All green = continue building.
Any red = fix before touching anything new.

---

### 9:30am – 12:00pm | Remaining Features + Tests

Continue the priority list from Friday night in the same order.
After each feature: write the test, run it, confirm green, then move on.

**Dispatch JSON payload display (30 minutes + test)**
```python
# tests/agents/test_briefing_agent.py

def test_dispatch_payload_structure_is_valid(
    brief_with_approval, briefing_agent
):
    """Dispatch payload must match DispatchPayload schema exactly."""
    payload = briefing_agent.build_dispatch(brief_with_approval, human_approved=True)
    assert payload.action_type in [
        "notify_department", "consolidate_311_crew",
        "suggest_ttc_short_turn", "surface_bike_share", "surface_parking"
    ]
    assert payload.priority in ["low", "medium", "high", "critical"]
    assert payload.requires_human_approval == True
    assert isinstance(payload.payload, dict)
    assert "message" in payload.payload

def test_dispatch_requires_human_approval(brief_fixture, briefing_agent):
    """Calling dispatch without approval must raise HumanApprovalRequired."""
    with pytest.raises(HumanApprovalRequired):
        briefing_agent.build_dispatch(brief_fixture, human_approved=False)
```

**Historical heatmap overlay (45 minutes + visual test)**
No unit test for the map rendering itself (that is a visual check).
But test that the data driving it is correct:
```python
# tests/integration/test_full_pipeline.py

def test_pipeline_emits_heatmap_data_when_historical_match_found(
    oct2024_scenario_events, mock_db
):
    """When cluster matches pattern_memory, pipeline state must include heatmap_data."""
    # Pre-populate pattern_memory with Bathurst pattern
    write_pattern_memory(mock_db, bathurst_pattern_fixture)
    
    state = run_pipeline(oct2024_scenario_events)
    
    # State must have heatmap overlay data for the matched cluster
    matched_briefs = [b for b in state.briefs if b.historical_match and b.historical_match.match_found]
    assert len(matched_briefs) > 0
```

**Live feeds running alongside replay (30 minutes + test)**
```python
# tests/integration/test_replay_scenario.py

def test_live_and_replay_events_both_appear_in_store(mock_db, oct2024_scenario):
    """Live feed events and replay events must coexist in the store."""
    # Write a live event
    live_event = make_event(source=EventSource.ROAD_RESTRICTION, event_id="live-001")
    write_events([live_event], db_path=mock_db)
    
    # Write a replay event
    replay_event = oct2024_scenario[0]
    write_events([replay_event], db_path=mock_db)
    
    # Both must be in the store
    all_events = fetch_recent_events(hours=24, db_path=mock_db)
    ids = [e.event_id for e in all_events]
    assert "live-001" in ids
    assert replay_event.event_id in ids
```

---

### 12:00 – 1:00pm | Full Eval Suite Run

Run the quality evals — these are the LLM-as-Judge tests that score reasoning.
These are slower (each calls qwen/Nemotron) so we run them deliberately.

```bash
python3 -m pytest evals/ -v --tb=short -s 2>&1 | tee eval_results_saturday.txt
cat eval_results_saturday.txt
```

**What to look for:**
- `test_correlation_quality` — average judge score must be ≥ 3.5/5
- `test_briefing_quality` — brief must score ≥ 3/5 on clarity, actionability, accuracy
- `test_adversarial` — all adversarial inputs handled without crash

Paste the full output. Any score below threshold means the prompt needs tuning.

**If correlation quality is below 3.5:**
The LLM is not reasoning clearly enough. Add one or two worked examples
to the CORRELATION_SYSTEM_PROMPT in specs/prompts.py:
```
Example of a CAUSAL cluster:
Events: [watermain break at Bathurst/Prue 06:55] [road closure at Bathurst/Prue 07:30]
Answer: {"is_causal": true, "confidence": 0.91, "cascade_type": "watermain_to_road", "reasoning": "Road closure appeared 35 minutes after watermain break at same intersection"}

Example of a NON-CAUSAL cluster:
Events: [graffiti complaint at King/John 09:00] [watermain break at Bathurst/Prue 09:15]
Answer: {"is_causal": false, "confidence": 0.05, "cascade_type": "unrelated", "reasoning": "Different event types 2km apart, no operational relationship"}
```

Re-run eval after any prompt change. Do not change prompts without re-running.

---

### 1:00 – 2:00pm | Lunch + Break

Eat. Go outside. 15 minutes of fresh air. You will think more clearly after.

---

### 2:00 – 5:00pm | Demo Preparation

**This time block is as important as any coding session.**

**Step 1 — Build the opening slide (30 minutes)**

Create a single image with three real screenshots side by side:
- Screenshot 1: toronto.ca/311 showing active water-related complaints
- Screenshot 2: The live road restrictions map with active closures
- Screenshot 3: ttc.ca service alerts showing a disruption

Add one line of text below:
*"Three real Toronto systems. Same corridor. Same day. Zero communication."*

Save as `demo_assets/opening_slide.png`.

**Step 2 — Write your exact demo script (45 minutes)**

Write word-for-word what you will say. Not an outline. Every sentence.

```
SLIDE (0:00 – 0:20)
"Toronto manages infrastructure across three completely separate systems.
When a watermain breaks, the road crew knows.
The TTC does not know. Three eleven does not know.
This is what that costs the city."

SWITCH TO DASHBOARD (0:20 – 0:40)
"This is StreetSense. Running right now on this hardware.
No cloud API. No data leaving city infrastructure."
[point to event pins]
"These are live Toronto infrastructure events. Four feeds. One view."

TRIGGER SCENARIO (0:40 – 1:00)
"Let me show you October second, twenty-twenty-four, Bathurst Street."
[trigger replay at speed_multiplier=10]
"Three watermain break reports. One hour. Same corridor."
[events appear on map]

CLUSTER FIRES (1:00 – 1:20)
"Watch the system reason."
[point to agent log panel]
Read aloud one log line: "Nemotron: is_causal=True, confidence zero point eight seven"
[cluster circle draws on map]

BRIEF AND SLACK (1:20 – 1:40)
[brief appears in sidebar]
"There is your operational brief."
[Slack notification arrives]
"And there is the notification going to Toronto Operations right now."
Read the first sentence of the brief aloud.

SEVERITY AND HISTORY (1:40 – 2:00)
[point to severity card]
"Severity eight out of ten."
"Estimated fourteen thousand commuters affected."
"TTC Route five-eleven."
[point to historical card]
"Similar pattern. October two thousand twenty-four.
Went uncoordinated for four hours. StreetSense would have caught it."

DISPATCH PAYLOAD (2:00 – 2:15)
[point to JSON payload on screen]
"This is the dispatch payload. Structured JSON.
Any city system — any department's software — consumes this.
This is the integration contract."

CLOSE (2:15 – 2:30)
"Toronto spends four hundred million dollars a year
reacting to infrastructure failures.
This is what proactive looks like.
One box. Inside city infrastructure. No cloud."
```

**Step 3 — Rehearse three times (1 hour)**

Run 1: Follow the script exactly. Time it. Must be under 2:45.

Run 2: Practice the recovery. Mid-demo, say "something went wrong" and:
- Trigger replay manually: `python3 -c "from dashboard.replay import trigger_replay; trigger_replay('oct2024_bathurst')"`
- Say: "Let me walk you through the October scenario directly."
- Sound intentional, not panicked.

Run 3: Stand up. Speak to the wall as if it is a room of judges.
The physical act of standing changes how you speak.

---

### 5:00 – 8:00pm | Final Polish Window

Only do these if demo rehearsal went smoothly.
If rehearsal revealed any problem, fix that first.

Low-effort high-impact polish:
- Agent log text size: make it slightly larger (readable from 3 meters)
- Add a pulsing "LIVE" indicator in the dashboard header
- Cluster circle: add a subtle animation when it first appears
- Map pin tooltips: show event type and time on hover
- Brief sidebar: make the severity score number large and bold

Do not do any of these:
- Add a new agent
- Change the LangGraph graph structure
- Change your prompts
- Refactor anything that works

**After any change: run the relevant test before moving on.**
```bash
# After any UI change — run the integration test to confirm nothing broke
python3 -m pytest tests/integration/ -v --tb=short
```

---

### 8:00 – 9:00pm | Final System Verification

Full end-to-end run. One more time. Fresh terminal.

```bash
# Stop everything
pkill -f "python3 main.py" 2>/dev/null || true

# Clean slate
export STREETSENSE_MODEL=nemotron-mini
python3 main.py &
sleep 5

# Run the integration test one final time
python3 -m pytest tests/integration/test_replay_scenario.py -v -s --tb=short
```

Paste the output. All green = done building.

If any test fails: fix only that specific test. Do not touch anything else.

Commit:
```bash
git add -A
git commit -m "saturday night final - all tests green"
git push
```

---

### 9:00 – 9:30pm | Sleep Preparation

Check all 6 demo checkboxes one final time in the browser:
- [ ] Events appear on map
- [ ] Agent log shows reasoning steps
- [ ] Cluster circle draws on map
- [ ] Brief appears in sidebar with severity 7+
- [ ] Slack notification arrives in #toronto-ops
- [ ] Historical match card shows

If all 6 checked: done. Close your laptop.
If one fails: fix it. If you cannot fix it in 20 minutes, revert to last commit.

**Sleep by 10:30pm.**

---

# SUNDAY MORNING
## 8:00am → Submission
### Goal: Confident demo, clean submission

---

### 8:00 – 8:20am | Final Verification

```bash
export STREETSENSE_MODEL=nemotron-mini

# One clean run of the full test suite
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

If green: great. Do not change anything.

If something broke overnight:
```bash
# Go back to Saturday night's last known good state
git stash
git log --oneline -5
# Find the "saturday night final" commit hash
git checkout [that hash]
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -10
```

A working product from 10pm Saturday is better than a broken product at 9am Sunday.

---

### 8:20 – 9:00am | Demo Rehearsal + Submit

One final rehearsal. Out loud. Standing up.

Specifically practice:
- The opening 20 seconds (your first impression)
- The Slack moment (say nothing — let the notification land)
- The closing 15 seconds (memorized, confident, no filler words)

Do not open your code editor during this 40 minutes.
Do not fix anything you notice. The time for fixing is over.

Submit whatever version is running and green.
After submitting: tell the people around you what you built in one sentence.
"StreetSense detects cascading infrastructure failures across Toronto
before city departments know they're connected."

If you can say that clearly in one breath, you are ready for judges.

---

## The Three Moments Judges Remember

**Moment 1 — The opening slide (0:00–0:20)**
Three real screenshots. One sentence. The problem is visceral before you say anything technical.

**Moment 2 — The Slack notification arrives (1:20–1:25)**
Brief appears AND Slack fires simultaneously. Say nothing. Let it land.
This is the "oh it actually does something" moment.

**Moment 3 — The closing line (2:15–2:30)**
"Four hundred million dollars reacting. This is proactive. One box. No cloud."
Memorized. Delivered to the room, not the screen.

---

## Risk Responses

| What goes wrong | Immediate action | What you say |
|---|---|---|
| Live feeds quiet or broken | Trigger oct2024_bathurst replay | "Let me show you October 2024 directly" |
| Brief does not fire automatically | Trigger replay manually | "Let me walk through this specific cascade" |
| Nemotron is slow | Let it run, narrate the agent log | "Watch the reasoning in real time" |
| Dashboard crashes | Open backup screen recording | Show recording, say "captured from earlier" |
| A test fails before demo | Revert to last green commit | Do not explain to judges, just demo what works |
| Judge: "why not use cloud?" | Answer directly | "City procurement requires data stays inside city infrastructure. That is the deployment story." |
| Judge: "what does this replace?" | Answer directly | "Nothing replaces it — no tool today correlates across these four separate feeds. That is the gap." |

---

## The Difference Between Working and Winning

**Working:**
Tests green. Replay fires. Brief generated. Runs on GB10.

**Winning:**
All of the above, PLUS the narrative makes judges feel the problem
before they see the solution. The Slack moment lands.
The historical card makes 2024 feel recent and real.
The closing line is delivered to the room, not mumbled at the screen.

More code does not make you win after Saturday 8pm.
Cleaner delivery does.
