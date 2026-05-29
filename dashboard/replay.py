"""
Replay engine — stream a scenario JSON file as if events are arriving live.
Used for demo when live feeds are quiet or unreliable.

Usage:
    python3 -m dashboard.replay oct2024_bathurst --speed 10
    python3 -m dashboard.replay queen_st_active --speed 60
"""
import asyncio
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from specs.data_contracts import UnifiedEvent
from state.graph import run_pipeline
from state.schema import PipelineState

logger = logging.getLogger(__name__)

MOCK_DATA = ROOT / "evals" / "mock_data"


def load_scenario(name: str) -> list[UnifiedEvent]:
    path = MOCK_DATA / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Scenario not found: {path}")
    data = json.loads(path.read_text())
    events = []
    for raw in data["events"]:
        try:
            events.append(UnifiedEvent(**raw))
        except Exception as e:
            logger.warning("Skipping event: %s", e)
    events.sort(key=lambda e: e.timestamp)
    logger.info("Loaded scenario '%s': %d events", data.get("scenario_id", name), len(events))
    return events


async def replay_scenario(
    scenario_name: str = "oct2024_bathurst",
    speed_multiplier: int = 10,
    push_fn=None,
) -> PipelineState:
    """
    Load scenario JSON, replay events in timestamp order with time-scaled delays.
    push_fn(event): called with each UnifiedEvent as it 'arrives'.
    After all events arrive, runs the full pipeline and returns final state.

    speed_multiplier=10  → 1 real minute = 6 seconds demo time
    speed_multiplier=60  → 1 real minute = 1 second demo time
    """
    events = load_scenario(scenario_name)

    logger.info("Replaying %s at %dx speed (%d events)", scenario_name,
                speed_multiplier, len(events))

    prev_ts = None
    for event in events:
        if prev_ts is not None:
            gap_seconds = (event.timestamp - prev_ts).total_seconds()
            sleep_time = max(0.5, gap_seconds / speed_multiplier)
            logger.debug("Sleeping %.1fs (real gap was %.0fs)", sleep_time, gap_seconds)
            await asyncio.sleep(sleep_time)

        logger.info("[%s] %s at %s",
                    event.timestamp.strftime("%H:%M"), event.event_type.value, event.address)
        if push_fn:
            await push_fn(event)

        prev_ts = event.timestamp

    # Run full pipeline on all arrived events
    logger.info("All events arrived — running pipeline…")
    state = run_pipeline([lambda: events])

    logger.info("Replay complete: %d briefs, severity max %s",
                len(state.briefs),
                max((b.severity_score for b in state.briefs), default=0))

    for brief in state.briefs:
        print(f"\n{'='*60}")
        print(f"[SEV {brief.severity_score}/10] {brief.headline}")
        print(f"\n{brief.body}")
        if brief.historical_match and brief.historical_match.match_found:
            print(f"\n📋 Historical: {brief.historical_match.similar_date} — "
                  f"uncoordinated {brief.historical_match.uncoordinated_hours}h")
        print(f"\nEst. commuters: {brief.estimated_commuters:,}")
        print("Actions:")
        for a in brief.recommended_actions:
            print(f"  • {a}")

    return state


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s — %(message)s")
    parser = argparse.ArgumentParser(description="Replay a StreetSense scenario")
    parser.add_argument("scenario", nargs="?", default="oct2024_bathurst")
    parser.add_argument("--speed", type=int, default=10,
                        help="Speed multiplier (default 10)")
    args = parser.parse_args()
    asyncio.run(replay_scenario(args.scenario, args.speed))
