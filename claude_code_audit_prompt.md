# Claude Code Audit Prompt
# Paste this directly into Claude Code

---

Please do a full audit of my StreetSense codebase against the original plan. I want to know exactly what is done correctly, what is missing, what is broken, and what can be improved before the hackathon this weekend.

## Step 1 — Read the plan documents first

Read these files in this order before looking at any code:

1. `plan.md` — the full spec-driven development plan with architecture, Pydantic models, agent designs, and agentic development rules
2. `streetsense_execution_plan.md` — the pre-event build sequence with what should be done on each day
3. `hackathon_weekend_plan.md` — the weekend execution plan including what "pre-event complete" means

After reading all three, you will know exactly what the system is supposed to do, how it is supposed to be built, and what the quality bar is.

## Step 2 — Read the entire codebase

Read every file in the project. Do not skip any file. Read them in this order:

1. `config.py`
2. `requirements.txt`
3. `specs/` — all files
4. `ingestion/` — all files recursively
5. `agents/` — all files
6. `state/` — all files
7. `tools/` — all files
8. `tests/` — all files recursively
9. `evals/` — all files recursively
10. `dashboard/` — all files recursively
11. `main.py`

If any of these directories or files do not exist, note that immediately — it means something from the plan was not built.

## Step 3 — Run the test suite

After reading all the code, run:

```bash
python3 -m pytest tests/ -v --tb=short 2>&1
```

Then run:

```bash
python3 -m pytest evals/test_adversarial.py -v --tb=short 2>&1
```

Show me the full output of both commands.

## Step 4 — Produce the audit report

Write a structured report with exactly these six sections. Be specific and direct. Do not be vague. If something is wrong, say exactly what is wrong and exactly what needs to change.

---

### SECTION 1: What was built correctly

For each component that matches the plan, list it with one sentence explaining why it is correct. Check against:

- Pydantic models match the data_contracts.py spec (all fields, all validators, all enums)
- Each agent has single responsibility (does not do more than its defined job)
- Tools are narrow and typed with proper docstrings
- LangGraph graph has explicit nodes and edges (no open while loops)
- max_iterations ceiling is enforced in the supervisor
- Circuit breaker is implemented
- Exceptions are caught and returned as text, not raised
- All LLM calls go through tools/llm_tools.py, not called directly
- MODEL is read from config, not hardcoded
- Tests exist alongside every implementation file
- Tests use mock data, not live feeds

---

### SECTION 2: What is missing entirely

For each item in the plan that does not exist in the codebase at all, list:
- What is missing
- Which plan document described it
- How critical it is (CRITICAL = blocks the demo, IMPORTANT = weakens the product, NICE = would improve it)

Check specifically for:
- Any Pydantic model from the spec that was not implemented
- Any agent that was planned but not built
- Any tool file that is absent
- Any test file that is absent
- The conftest.py with shared fixtures
- The pytest.ini configuration
- The oct2024_bathurst.json scenario file
- The queen_st_active.json scenario file
- The adversarial mock data files (malformed_feed.json, outside_toronto.json, single_event.json)
- The memory_agent.py for overnight pattern learning
- The replay.py in dashboard/
- The .env.example file

---

### SECTION 3: What exists but does not match the plan

For each component that exists but deviates from the spec, list:
- What the plan said it should do
- What it actually does
- Whether the deviation is acceptable or needs to be fixed

Check specifically for:
- Pydantic model fields that are different from the spec (wrong types, missing validators, missing fields)
- Agents that do more than one job (violating SRP)
- Tools that take more than 3 parameters (violating ACI)
- LLM calls made directly in agent files instead of through llm_tools.py
- Hardcoded model names instead of reading from config.MODEL
- Open while loops instead of LangGraph nodes
- Missing max_iterations ceiling
- Tests that call live feeds instead of using mock data
- Tests that do not cover edge cases or adversarial inputs
- Missing HumanApprovalRequired check before dispatch

---

### SECTION 4: Test coverage gaps

Run this and show the output:
```bash
python3 -m pytest tests/ --co -q 2>&1
```

Then assess:
- Which source files have no corresponding test file
- Which functions have no test (look for obvious untested paths)
- Which edge cases are not covered:
  - Empty input to every function that accepts a list
  - None input to every function that accepts optional types
  - Malformed JSON input to every feed parser
  - Out-of-Toronto coordinates to the UnifiedEvent validator
  - Network failure to every function that calls requests.get
  - DB write failure to every function that calls write_events
  - LLM timeout or parse failure to every function that calls the LLM
- Which adversarial paths are not tested

For each gap, write the exact test function signature and assertion that should be added.

---

### SECTION 5: What can be improved

For each improvement, write:
- What to change
- Why it matters for the hackathon (demo impact, robustness, or judge impressiveness)
- How long it will take to implement (quick = under 30 min, medium = 30-90 min, long = over 90 min)
- Whether to do it before Friday or skip it

Evaluate specifically:
- Are the LLM prompts in specs/prompts.py tight enough? Do they enforce JSON-only output? Do they include examples?
- Is the correlation agent prompt likely to return clean JSON on Nemotron or will it need adjustment?
- Is the severity scoring formula correct and auditable?
- Does the briefing agent produce output that sounds like something a city supervisor would actually read?
- Is the Slack notification payload formatted well enough to impress judges?
- Is the agent log panel emitting enough messages to show visible reasoning?
- Is the replay mode fast enough at speed_multiplier=10 (events should appear every 3-5 seconds, not all at once)?
- Is the dashboard WebSocket connection resilient to brief disconnects?
- Is the Bathurst scenario JSON file complete enough to tell the cascade story clearly?

---

### SECTION 6: Priority action list for today

Based on everything above, give me an ordered list of exactly what to fix and build today before Friday evening. Format as:

```
PRIORITY 1 — [name] — [CRITICAL/IMPORTANT/NICE] — [time estimate]
What to do: [exact description]
Why now: [one sentence]

PRIORITY 2 — [name] — [CRITICAL/IMPORTANT/NICE] — [time estimate]
What to do: [exact description]
Why now: [one sentence]

... and so on
```

Stop the list at whatever can realistically be completed before Friday evening.
Do not include anything that would take longer than 2 hours unless it is CRITICAL.

---

## What I need from you

Do not summarize or be vague. If a test is failing, show me the exact error. If a Pydantic model is missing a field, name the field. If a prompt is weak, show me the weak line and the improved version.

After the report, ask me: "Which priority item do you want to fix first?" Then we will fix them one at a time, with tests, before moving to the next one.
