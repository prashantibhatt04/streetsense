"""
SupervisorAgent — the only agent with visibility across the full pipeline.
Responsibilities: receive trigger, delegate to specialized agents,
enforce iteration ceiling, handle circuit breaker.
Does NOT reason about events — only routes.
"""
import logging
from datetime import datetime, timezone
from state.schema import PipelineState
from state.graph import run_pipeline
from state import agent_log
from config import MAX_AGENT_ITERATIONS

logger = logging.getLogger(__name__)


class SupervisorAgent:
    """
    Routes pipeline execution. Wraps run_pipeline with circuit-breaker
    logging and provides a clean entry point for external callers.
    """

    def __init__(self):
        self._run_count = 0

    def run(self, fetch_fns: list, trigger: str = "manual") -> PipelineState:
        """
        Execute one pipeline cycle.
        trigger: human-readable reason for this run (e.g. "scheduled", "manual", "event_detected")
        """
        self._run_count += 1
        agent_log.append(f"Supervisor: starting run #{self._run_count} (trigger={trigger})")
        logger.info("Supervisor run #%d triggered by: %s", self._run_count, trigger)

        state = run_pipeline(fetch_fns)

        if state.errors:
            agent_log.append(
                f"Supervisor: run complete with {len(state.errors)} error(s)"
            )
            logger.warning("Run #%d completed with errors: %s",
                           self._run_count, state.errors[:3])
        else:
            agent_log.append(
                f"Supervisor: run complete — "
                f"{len(state.briefs)} brief(s), "
                f"{len(state.dispatch_payloads)} dispatch payload(s)"
            )
            logger.info("Run #%d complete — briefs=%d, payloads=%d",
                        self._run_count, len(state.briefs),
                        len(state.dispatch_payloads))

        return state
