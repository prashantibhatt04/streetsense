# StreetSense — Agent State Machine

## Nodes

```
START
  │
ingest_node        → IngestionAgent / fetch_fns
  │
cluster_node       → geo_tools.cluster_events (deterministic, no LLM)
  │
correlate_node     → CorrelationAgent (LLM — gemma4 / nemotron)
  │
impact_node        → ImpactAgent (deterministic scoring + LLM for duration)
  │
brief_node         → BriefingAgent (LLM — city supervisor brief)
  │
dispatch_node      → build_dispatch_batch (deterministic from brief)
  │
END
```

## Edges (deterministic conditions)

```
START        → ingest_node    [always]
ingest_node  → cluster_node  [condition: iteration_count < 10]
ingest_node  → END (error)   [condition: iteration_count >= 10 — circuit breaker]
cluster_node → correlate_node [condition: clusters found >= 1]
cluster_node → END            [condition: no clusters]
correlate_node → impact_node  [condition: any correlation returned]
correlate_node → END          [condition: no clusters to correlate]
impact_node  → brief_node     [condition: any impact assessed]
impact_node  → END            [condition: nothing to assess]
brief_node   → dispatch_node  [always — dispatch filters by severity internally]
dispatch_node → END
```

## Circuit Breaker

Any node checks `state.is_stuck()` (iteration_count >= 10) at entry.  
On trigger: appends error to `state.errors`, returns immediately without processing.

## HITL Gate

`DispatchPayload.requires_human_approval = True` on all payloads.  
`dispatch_tools.emit_dispatch_payload()` raises `HumanApprovalRequired` if called without approval.  
In demo mode: auto-approved (visible 3s pause).  
In production: supervisor button click in dashboard sets `state.human_approved = True`.

## Overnight Memory Loop

```
START (cron 02:00)
  │
memory_node → MemoryAgent.run_nightly()
  │          Reads cluster_log for yesterday
  │          Writes pattern records to pattern_memory
  │          Increments confidence for repeating patterns
END
```

## State Object (PipelineState)

| Field | Type | Set by |
|-------|------|--------|
| run_id | str | SupervisorAgent on init |
| raw_events | list[UnifiedEvent] | ingest_node |
| clusters | list[ClusterCandidate] | cluster_node |
| correlations | list[CorrelationResult] | correlate_node |
| impacts | list[ImpactAssessment] | impact_node |
| briefs | list[OperationalBrief] | brief_node |
| dispatch_payloads | list[DispatchPayload] | dispatch_node |
| human_approved | bool | HITL gate |
| iteration_count | int | incremented at each node |
| errors | list[str] | any node on exception |

## Feed Sources

| Feed | URL | Poll interval | Geocoding needed |
|------|-----|--------------|-----------------|
| Road Restrictions | secure.toronto.ca/opendata/cart | 120s | No |
| TTC Alerts | gtfsrt.ttc.ca/alerts/all | 120s | No (route centroid) |
| Utility Cuts | ckan0.cf.opendata.inter.prod-toronto.ca | 3600s | Yes |
| 311 Requests | ckan0.cf.opendata.inter.prod-toronto.ca | 3600s | Yes |
