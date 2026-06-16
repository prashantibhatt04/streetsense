"""
Proactive cascade prediction agent.

Given a single early 311 watermain_break or flooding event, predicts what road
closures and transit disruptions are likely and recommends dispatches for
supervisor approval — before the situation escalates.
"""
import logging
import uuid
from specs.data_contracts import (
    UnifiedEvent, EventType, PredictedCascade, DispatchRecommendation,
)
from tools.llm_tools import call_llm_json
from tools.gtfs_tools import routes_near_coords as _gtfs_routes_near
from config import MODEL
from state import agent_log

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 2

# Street keyword → TTC route metadata
STREET_TO_ROUTES: dict[str, dict] = {
    "bathurst": {"route": "511", "type": "streetcar"},
    "king":     {"route": "504", "type": "streetcar"},
    "queen":    {"route": "501", "type": "streetcar"},
    "spadina":  {"route": "510", "type": "streetcar"},
    "dundas":   {"route": "505", "type": "streetcar"},
    "college":  {"route": "506", "type": "streetcar"},
    "st clair": {"route": "512", "type": "streetcar"},
    "finch":    {"route": "39",  "type": "bus"},
    "sheppard": {"route": "85",  "type": "bus"},
    "eglinton": {"route": "32",  "type": "bus"},
}

_TRIGGERING_TYPES = {EventType.WATERMAIN_BREAK, EventType.FLOODING, EventType.SEWER_BACKUP}

_VALID_DISPATCH_TYPES = {"water_repair", "ttc_diversion", "road_closure", "notify_department"}
_VALID_PRIORITIES     = {"HIGH", "MEDIUM", "LOW"}


def affected_routes_from_address(address: str) -> list[dict]:
    """Return STREET_TO_ROUTES entries whose street keyword appears in the address."""
    lower = address.lower()
    return [meta for street, meta in STREET_TO_ROUTES.items() if street in lower]


def _is_night_route(short_name: str) -> bool:
    """TTC night buses have route numbers 300–399 — exclude them from daytime predictions."""
    try:
        n = int(short_name)
        return 300 <= n <= 399
    except ValueError:
        return False


def _get_route_hint(event: UnifiedEvent) -> str:
    """
    Build the TTC route hint for the LLM prompt.
    Keyword-based corridor matches (STREET_TO_ROUTES) always come first —
    these capture the primary streetcar line on the street. GTFS spatial
    lookup adds any additional nearby routes within radius. Night routes
    (300-399) are excluded — they run only overnight.
    """
    keyword = affected_routes_from_address(event.address)
    gtfs = _gtfs_routes_near(event.latitude, event.longitude)

    routes: list[str] = []
    seen: set[str] = set()

    for r in keyword:
        if r["route"] not in seen:
            routes.append(f"route {r['route']} ({r['type']})")
            seen.add(r["route"])

    if gtfs:
        for r in gtfs:
            if _is_night_route(r["short_name"]):
                continue
            if r["short_name"] not in seen:
                routes.append(f"route {r['short_name']} {r['long_name']} ({r['route_type']})")
                seen.add(r["short_name"])

    if routes:
        return ", ".join(routes)
    return "no known TTC route on this street"


_FLOOD_DEPT_HINT = (
    "Department guidance: for flooding/drainage use 'Toronto Water — Stormwater Operations' "
    "(catch basins, pumps, overflow). Do NOT use 'Water Main' or 'Toronto Water — Water Supply' — "
    "those are for pipe breaks only."
)
_SEWER_DEPT_HINT = (
    "Department guidance: for sewer backup use 'Toronto Water — Wastewater Operations'. "
    "Do NOT use 'Water Main' — this is a drainage/sewer issue, not a pipe break."
)

_DEPT_HINT: dict[EventType, str] = {
    EventType.FLOODING: _FLOOD_DEPT_HINT,
    EventType.SEWER_BACKUP: _SEWER_DEPT_HINT,
}


def build_prediction_prompt(event: UnifiedEvent) -> str:
    route_hint = _get_route_hint(event)
    dept_hint = _DEPT_HINT.get(event.event_type, "")
    dept_line = f"\n{dept_hint}" if dept_hint else ""
    return f"""A 311 service request has just been received. Respond in JSON only — no explanation outside the JSON.

Type: {event.event_type.value}
Location: {event.address}
Description: {event.description}
Time: {event.timestamp.strftime('%H:%M UTC')}
Known TTC routes on this street: {route_hint}{dept_line}

Based on this single early report:
1. What road closures are likely in the next 1-2 hours?
2. Which TTC routes are at risk of disruption?
3. What should be dispatched proactively RIGHT NOW before the situation escalates?

Respond with this exact JSON (all fields required):
{{
  "predicted_impacts": ["<impact 1>", "<impact 2>"],
  "recommended_dispatches": [
    {{
      "dispatch_type": "<one of: water_repair | ttc_diversion | road_closure | notify_department>",
      "target_department": "<department name>",
      "message": "<actionable message for that department>",
      "priority": "<HIGH | MEDIUM | LOW>"
    }}
  ],
  "confidence": <0.0–1.0>,
  "reasoning": "<one or two sentences>"
}}"""


def _make_dispatch_id(trigger_event_id: str, dispatch_type: str) -> str:
    return f"pred-{trigger_event_id}-{dispatch_type}"


def parse_llm_response(raw: dict, trigger_event_id: str) -> PredictedCascade | None:
    """Parse raw LLM dict into a PredictedCascade. Returns None on any validation error."""
    try:
        confidence = float(raw.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        dispatches = []
        for d in (raw.get("recommended_dispatches") or []):
            dtype = d.get("dispatch_type", "notify_department")
            if dtype not in _VALID_DISPATCH_TYPES:
                dtype = "notify_department"
            priority = str(d.get("priority", "MEDIUM")).upper()
            if priority not in _VALID_PRIORITIES:
                priority = "MEDIUM"
            dispatches.append(DispatchRecommendation(
                dispatch_id=_make_dispatch_id(trigger_event_id, dtype),
                dispatch_type=dtype,
                target_department=str(d.get("target_department", "City Operations")),
                message=str(d.get("message", "")),
                priority=priority,
            ))

        return PredictedCascade(
            trigger_event_id=trigger_event_id,
            predicted_impacts=list(raw.get("predicted_impacts") or []),
            recommended_dispatches=dispatches,
            confidence=confidence,
            reasoning=str(raw.get("reasoning", "")),
        )
    except Exception as e:
        logger.warning("Failed to parse prediction LLM response: %s", e)
        return None


def _heuristic_fallback(event: UnifiedEvent) -> PredictedCascade:
    """Deterministic fallback when LLM is unavailable — event-type-aware, GTFS routes."""
    gtfs = _gtfs_routes_near(event.latitude, event.longitude)
    all_routes = (
        [{"route": r["short_name"], "type": r["route_type"]} for r in gtfs
         if not _is_night_route(r["short_name"])]
        if gtfs else affected_routes_from_address(event.address)
    )

    location = event.address.split(",")[0]
    if event.event_type == EventType.FLOODING:
        impacts: list[str] = [
            f"Emergency road closure required at {location} — active flooding",
            "Stormwater / drainage system overwhelmed — pumping deployment needed",
        ]
        dispatches: list[DispatchRecommendation] = [
            DispatchRecommendation(
                dispatch_id=_make_dispatch_id(event.event_id, "road_closure"),
                dispatch_type="road_closure",
                target_department="Transportation Services / Police",
                message=(
                    f"EMERGENCY: Close {location} immediately — active road flooding reported. "
                    "Deploy physical barriers and divert all traffic."
                ),
                priority="HIGH",
            ),
            DispatchRecommendation(
                dispatch_id=_make_dispatch_id(event.event_id, "water_repair"),
                dispatch_type="water_repair",
                target_department="Toronto Water — Stormwater Operations",
                message=(
                    f"Stormwater / drainage system overwhelmed at {location}. "
                    "Deploy pumping units and assess catch basin blockage. "
                    "Do NOT send pipe-repair crew — this is surface flooding, not a watermain break."
                ),
                priority="HIGH",
            ),
        ]
        ttc_reason = f"flooding at {location}"
    elif event.event_type == EventType.SEWER_BACKUP:
        impacts = [
            f"Sewer / wastewater system overwhelmed at {location}",
            "Risk of sewer overflow onto adjacent streets and infrastructure",
        ]
        dispatches = [
            DispatchRecommendation(
                dispatch_id=_make_dispatch_id(event.event_id, "water_repair"),
                dispatch_type="water_repair",
                target_department="Toronto Water — Wastewater Operations",
                message=(
                    f"Sewer system overwhelmed at {location}. "
                    "Deploy inspection crew — assess capacity and overflow risk."
                ),
                priority="HIGH",
            ),
            DispatchRecommendation(
                dispatch_id=_make_dispatch_id(event.event_id, "notify_department"),
                dispatch_type="notify_department",
                target_department="Emergency Management",
                message=(
                    f"Sewer backup reported at {location}. "
                    "Assess risk of escalation to street flooding or infrastructure ingress."
                ),
                priority="MEDIUM",
            ),
        ]
        ttc_reason = f"sewer overflow risk at {location}"
    else:
        impacts = [
            f"Road closure likely on {location} within 1-2 hours",
        ]
        dispatches = [
            DispatchRecommendation(
                dispatch_id=_make_dispatch_id(event.event_id, "water_repair"),
                dispatch_type="water_repair",
                target_department="Toronto Water",
                message=f"Dispatch repair crew to {event.address} — possible watermain break reported",
                priority="HIGH",
            ),
            DispatchRecommendation(
                dispatch_id=_make_dispatch_id(event.event_id, "road_closure"),
                dispatch_type="road_closure",
                target_department="Transportation Services",
                message=f"Prepare traffic control for {location} — watermain repair likely requires lane closure",
                priority="MEDIUM",
            ),
        ]
        ttc_reason = f"watermain break reported at {location}"

    if all_routes:
        route_list = ", ".join(f"route {r['route']} ({r['type']})" for r in all_routes)
        for r in all_routes:
            impacts.append(f"TTC route {r['route']} ({r['type']}) at risk of diversion")
        dispatches.append(DispatchRecommendation(
            dispatch_id=_make_dispatch_id(event.event_id, "ttc_diversion"),
            dispatch_type="ttc_diversion",
            target_department="TTC Operations",
            message=f"Pre-emptively prepare diversion plan for {route_list} — {ttc_reason}",
            priority="MEDIUM",
        ))

    return PredictedCascade(
        trigger_event_id=event.event_id,
        predicted_impacts=impacts,
        recommended_dispatches=dispatches,
        confidence=0.5,
        reasoning="Heuristic fallback: LLM unavailable. Based on event type and GTFS spatial lookup.",
    )


def predict_cascade(event: UnifiedEvent) -> PredictedCascade | None:
    """
    Predict cascade for a single early event.
    Returns None for event types that don't trigger cascades.
    Never raises.
    """
    if event.event_type not in _TRIGGERING_TYPES:
        return None

    agent_log.append(
        f"Predicting cascade for {event.event_type.value} at {event.address.split(',')[0]}…"
    )

    prompt = build_prediction_prompt(event)

    for attempt in range(MAX_ITERATIONS):
        try:
            raw = call_llm_json(prompt)
        except Exception as e:
            logger.warning("LLM call raised on attempt %d: %s", attempt + 1, e)
            continue
        if not raw:
            logger.warning("Empty prediction response on attempt %d", attempt + 1)
            continue
        result = parse_llm_response(raw, event.event_id)
        if result:
            agent_log.append(
                f"Prediction: confidence={result.confidence:.2f}, "
                f"{len(result.recommended_dispatches)} dispatch(es) recommended"
            )
            return result

    agent_log.append("Prediction LLM failed — using heuristic fallback")
    return _heuristic_fallback(event)


def predict_batch(events: list[UnifiedEvent]) -> list[PredictedCascade]:
    """Run predict_cascade on each event, skipping non-triggering types. Never raises."""
    results = []
    for event in events:
        prediction = predict_cascade(event)
        if prediction is not None:
            results.append(prediction)
    return results
