"""
StreetSense entry point.

Modes:
  python3 main.py                  # single run against live feeds
  python3 main.py --mode db        # single run from local DB
  python3 main.py --mode daemon    # continuous loop, reads DB every N seconds
  python3 main.py --mode daemon --interval 60  # custom poll interval

The daemon mode is the intended production deployment mode for a government box:
  1. Run scripts/seed_db.py once (or on a cron) to populate the DB
  2. Run python3 main.py --mode daemon in the background
  3. Open http://localhost:5001 — data updates every interval seconds
"""

import argparse
import logging
import signal
import sys
import time
from state.graph import run_pipeline
from state.schema import PipelineState
from ingestion.geocoder import geocode_address
from ingestion.feeds.road_restrictions import fetch_road_restrictions
from ingestion.feeds.ttc_alerts import fetch_ttc_alerts
from ingestion.feeds.utility_cuts import fetch_utility_cuts
from ingestion.feeds.requests_311 import fetch_311_requests
from tools.db_tools import fetch_all_from_db, write_cluster_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_shutdown_requested = False


def _request_shutdown(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True


def build_live_feed_fns() -> list:
    return [
        fetch_road_restrictions,
        fetch_ttc_alerts,
        lambda: fetch_utility_cuts(geocode_fn=geocode_address, limit=100),
        lambda: fetch_311_requests(geocode_fn=geocode_address, limit=100),
    ]


def build_db_feed_fns(hours: float = 72) -> list:
    """Read from local DB instead of hitting live APIs."""
    return [lambda: fetch_all_from_db(hours=hours)]


def _persist(state: PipelineState) -> None:
    corr_map = {c.cluster_id: c for c in state.correlations}
    for brief in state.briefs:
        corr = corr_map.get(brief.cluster_id)
        write_cluster_result(
            cluster_id=brief.cluster_id,
            run_id=state.run_id,
            cascade_type=corr.cascade_type if corr else "unrelated",
            severity_score=brief.severity_score,
            brief_headline=brief.headline,
            brief_body=brief.body,
        )


def print_summary(state: PipelineState) -> None:
    print("\n" + "=" * 60)
    print(f"Run: {state.run_id}  started: {state.started_at.strftime('%H:%M:%S UTC')}")
    print(f"Events: {len(state.raw_events)}  clusters: {len(state.clusters)}"
          f"  briefs: {len(state.briefs)}  errors: {len(state.errors)}")
    for brief in state.briefs:
        print(f"\n  [SEV {brief.severity_score}/10] {brief.headline}")
        for action in brief.recommended_actions[:2]:
            print(f"    • {action}")
    if state.errors:
        for e in state.errors[:3]:
            print(f"  ERROR: {e}")
    print("=" * 60)


def run_once(feed_fns: list) -> PipelineState:
    state = run_pipeline(feed_fns)
    _persist(state)
    print_summary(state)
    return state


def run_daemon(feed_fns: list, interval: int) -> None:
    global _shutdown_requested
    _shutdown_requested = False
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)
    logger.info("Daemon started — polling every %ds. Press Ctrl-C to stop.", interval)
    logger.info("Dashboard: http://localhost:5001")
    run_count = 0
    _prev_brief_ids: set = set()
    while not _shutdown_requested:
        run_count += 1
        logger.info("--- Cycle #%d  %s ---", run_count,
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        try:
            state = run_once(feed_fns)
            current_ids = {b.brief_id for b in state.briefs}
            new_ids = current_ids - _prev_brief_ids
            if new_ids:
                logger.info("*** %d NEW brief(s) this cycle ***", len(new_ids))
                for brief in state.briefs:
                    if brief.brief_id in new_ids:
                        logger.info("  NEW [SEV %d] %s", brief.severity_score, brief.headline)
            _prev_brief_ids = current_ids
            logger.info("Next run in %ds.", interval)
        except KeyboardInterrupt:
            _shutdown_requested = True
        except Exception as e:
            logger.error("Cycle failed: %s — retrying next interval", e)
        if not _shutdown_requested:
            for _ in range(interval):
                if _shutdown_requested:
                    break
                time.sleep(1)
    logger.info("Stopped after %d cycle(s). Goodbye.", run_count)


def main() -> int:
    parser = argparse.ArgumentParser(description="StreetSense pipeline runner")
    parser.add_argument(
        "--mode", choices=["live", "db", "daemon"], default="live",
        help="live = hit Toronto APIs | db = read local SQLite | daemon = continuous db loop",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Poll all four live Toronto feeds continuously (default interval: 300s)",
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Poll interval in seconds for --watch or --mode daemon (default: 300 watch / 120 daemon)",
    )
    parser.add_argument(
        "--hours", type=float, default=72,
        help="How far back to read events from DB in hours (default: 72)",
    )
    args = parser.parse_args()

    if args.watch:
        interval = args.interval or 300
        logger.info("Watch mode — polling live feeds every %ds. Ctrl-C to stop.", interval)
        run_daemon(build_live_feed_fns(), interval)
        return 0

    if args.mode == "live":
        logger.info("Mode: live — fetching from Toronto open data APIs")
        state = run_once(build_live_feed_fns())
        return 1 if state.errors else 0

    elif args.mode == "db":
        logger.info("Mode: db — reading from local SQLite (air-gapped)")
        state = run_once(build_db_feed_fns(hours=args.hours))
        return 1 if state.errors else 0

    elif args.mode == "daemon":
        interval = args.interval or 120
        logger.info("Mode: daemon — continuous loop from local DB every %ds", interval)
        logger.info("Seed the DB first if empty: python3 -m scripts.seed_db")
        run_daemon(build_db_feed_fns(hours=args.hours), interval)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
