"""
Replay a mock scenario through the full pipeline without hitting live feeds.
Usage: python3 -m evals.replay oct2024_bathurst
"""
import json
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
from specs.data_contracts import UnifiedEvent
from state.graph import prediction_node, cluster_node, correlate_node, impact_node, brief_node, dispatch_node
from state.schema import PipelineState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

MOCK_DATA = Path(__file__).parent / "mock_data"


def load_scenario(name: str) -> list[UnifiedEvent]:
    path = MOCK_DATA / f"{name}.json"
    if not path.exists():
        print(f"Scenario not found: {path}")
        sys.exit(1)
    data = json.loads(path.read_text())
    events = []
    for raw in data["events"]:
        try:
            events.append(UnifiedEvent(**raw))
        except Exception as e:
            logger.warning("Skipping event: %s", e)
    print(f"Loaded scenario '{data['scenario_id']}': {data['description']}")
    print(f"Events loaded: {len(events)}")
    return events


def replay(scenario_name: str) -> PipelineState:
    events = load_scenario(scenario_name)

    state = PipelineState(
        run_id=f"replay-{scenario_name}",
        started_at=datetime.now(timezone.utc),
    ).with_events(events)

    state = prediction_node(state)
    state = cluster_node(state)
    state = correlate_node(state)
    state = impact_node(state)
    state = brief_node(state)
    state = dispatch_node(state)

    print(f"\nClusters:     {len(state.clusters)}")
    print(f"Correlations: {len(state.correlations)}")
    print(f"Impacts:      {len(state.impacts)}")
    print(f"Briefs:       {len(state.briefs)}")
    print(f"Dispatches:   {len(state.dispatch_payloads)}")
    print(f"Predictions:  {len(state.predicted_cascades)}")

    print(f"\n{'='*60}")
    print("CORRELATION RESULTS")
    for c in state.correlations:
        print(f"  is_causal:  {c.is_causal}")
        print(f"  confidence: {c.confidence:.2f}")
        print(f"  cascade:    {c.cascade_type}")
        print(f"  chain:      {c.causal_chain}")
        print(f"  reasoning:  {c.reasoning}")

    for brief in state.briefs:
        print(f"\n{'='*60}")
        print(f"[Severity {brief.severity_score}/10] {brief.headline}")
        print(f"\n{brief.body}")
        print("\nRecommended actions:")
        for action in brief.recommended_actions:
            print(f"  • {action}")

    if state.errors:
        print(f"\nErrors: {state.errors}")

    return state


if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else "oct2024_bathurst"
    replay(scenario)
