"""
MemoryAgent — overnight batch job.
Single responsibility: review yesterday's cluster outcomes, detect confirmed cascades,
write pattern records so BriefingAgent can surface historical matches.

Runs via cron at 02:00 or manually: python3 -m agents.memory_agent
Never runs during live demo — output is read-only from perspective of live pipeline.
"""
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "streetsense.db"


class MemoryAgent:
    """
    Reviews cluster_log from the past 24h and persists outcome patterns
    into pattern_memory for future correlation with historical context.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def run_nightly(self) -> int:
        """
        Review yesterday's clusters. For each:
          1. Extract cascade_type and corridor from cluster_log
          2. Write/update pattern_memory row
          3. Increment confidence if pattern repeats
        Returns number of patterns written.
        """
        if not self.db_path.exists():
            logger.warning("DB not found — nothing to process")
            return 0

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        clusters = self._fetch_yesterday_clusters(yesterday)
        if not clusters:
            logger.info("No cluster_log entries for %s", yesterday)
            return 0

        logger.info("Processing %d clusters from %s", len(clusters), yesterday)
        written = 0
        for row in clusters:
            if self._write_pattern(row, yesterday):
                written += 1

        logger.info("MemoryAgent: wrote %d pattern(s) to pattern_memory", written)
        return written

    def _fetch_yesterday_clusters(self, date: str) -> list:
        try:
            conn = sqlite3.connect(str(self.db_path))
            rows = conn.execute(
                """SELECT cluster_id, cascade_type, severity_score, brief_headline
                   FROM cluster_log
                   WHERE date(created_at) = ? AND cascade_type != 'unrelated'
                """,
                (date,),
            ).fetchall()
            conn.close()
            return rows
        except Exception as e:
            logger.error("Failed to fetch cluster_log: %s", e)
            return []

    def _extract_corridor(self, headline: str) -> str:
        keywords = ["bathurst", "queen", "king", "dundas", "spadina",
                    "college", "bloor", "yonge", "avenue", "st clair"]
        h = headline.lower()
        for kw in keywords:
            if kw in h:
                return kw
        return "toronto"

    def _write_pattern(self, row: tuple, date: str) -> bool:
        cluster_id, cascade_type, severity_score, headline = row
        corridor = self._extract_corridor(headline or "")

        try:
            conn = sqlite3.connect(str(self.db_path))

            # Check if pattern already exists for this cascade + corridor
            existing = conn.execute(
                """SELECT id, confidence FROM pattern_memory
                   WHERE cascade_type = ? AND corridor = ?
                   ORDER BY id DESC LIMIT 1""",
                (cascade_type, corridor),
            ).fetchone()

            if existing:
                # Increment confidence (max 0.99)
                new_conf = min(0.99, (existing[1] or 0.5) + 0.05)
                conn.execute(
                    "UPDATE pattern_memory SET confidence=? WHERE id=?",
                    (new_conf, existing[0]),
                )
                logger.debug("Updated pattern confidence for %s/%s → %.2f",
                             cascade_type, corridor, new_conf)
            else:
                outcome = (
                    f"Severity {severity_score}/10 cascade detected on {corridor.title()} corridor. "
                    f"{headline}"
                )
                conn.execute(
                    """INSERT INTO pattern_memory
                       (cascade_type, corridor, similar_date, outcome, uncoordinated_hours, confidence)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (cascade_type, corridor, date, outcome, 2.0, 0.6),
                )
                logger.info("New pattern: %s on %s (%s)", cascade_type, corridor, date)

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("Failed to write pattern: %s", e)
            return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s — %(message)s")
    agent = MemoryAgent()
    n = agent.run_nightly()
    print(f"MemoryAgent complete: {n} pattern(s) written")
