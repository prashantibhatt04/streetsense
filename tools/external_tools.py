"""
ACI tools for external integrations: Bike Share Toronto, Slack notifications.
Each tool does exactly one thing, takes ≤ 3 parameters, and never raises.
"""

import logging
import os
import requests
from math import radians, sin, cos, sqrt, atan2
from specs.data_contracts import OperationalBrief

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M = 6_371_000


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = _EARTH_RADIUS_M
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def fetch_bikeshare_nearby(lat: float, lng: float, radius_m: int = 500) -> list[dict]:
    """
    Fetch Bike Share Toronto stations within radius_m metres of lat/lng.
    Uses the public GBFS feed — no API key required.
    Returns list of {name, available_bikes, lat, lng, distance_m}, sorted by distance.
    Returns [] on any failure — never raises.
    """
    try:
        status_resp = requests.get(
            "https://tor.publicbikesystem.net/customer/gbfs/v2/en/station_status",
            timeout=5,
        )
        info_resp = requests.get(
            "https://tor.publicbikesystem.net/customer/gbfs/v2/en/station_information",
            timeout=5,
        )
        status_resp.raise_for_status()
        info_resp.raise_for_status()

        stations_info = {
            s["station_id"]: s
            for s in info_resp.json()["data"]["stations"]
        }
        results = []
        for status in status_resp.json()["data"]["stations"]:
            info = stations_info.get(status["station_id"])
            if not info:
                continue
            dist = _haversine(lat, lng, info["lat"], info["lon"])
            if dist <= radius_m:
                results.append({
                    "name": info["name"],
                    "available_bikes": status.get("num_bikes_available", 0),
                    "lat": info["lat"],
                    "lng": info["lon"],
                    "distance_m": int(dist),
                })
        return sorted(results, key=lambda x: x["distance_m"])[:5]
    except Exception as e:
        logger.warning("Bike Share fetch failed: %s", e)
        return []


def emit_slack_notification(brief: OperationalBrief, webhook_url: str = "") -> bool:
    """
    Post an operational brief to a Slack channel via incoming webhook.
    Reads SLACK_WEBHOOK_URL from environment if webhook_url is not provided.
    Returns True on HTTP 200, False on any failure — never raises.
    """
    url = webhook_url or os.getenv("SLACK_WEBHOOK_URL", "")
    if not url:
        logger.warning("SLACK_WEBHOOK_URL not set — skipping Slack notification")
        return False

    sev = brief.severity_score
    sev_emoji = "🚨" if sev >= 7 else "⚠️" if sev >= 4 else "ℹ️"
    routes = ", ".join(brief.recommended_actions[:2]) if brief.recommended_actions else "—"

    payload = {
        "text": f"{sev_emoji} *StreetSense Alert — Severity {sev}/10*",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{sev_emoji} StreetSense — Severity {sev}/10",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{brief.headline}*",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": brief.body[:300] if brief.body else "—",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:* {sev}/10"},
                    {"type": "mrkdwn", "text": f"*Actions:* {routes}"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Cluster `{brief.cluster_id}` · {brief.source_event_count} events · StreetSense",
                    }
                ],
            },
        ],
    }

    try:
        resp = requests.post(url, json=payload, timeout=5)
        if resp.status_code == 200:
            logger.info("Slack notification sent for cluster %s", brief.cluster_id)
            return True
        logger.warning("Slack returned HTTP %d: %s", resp.status_code, resp.text[:100])
        return False
    except Exception as e:
        logger.warning("Slack notification failed: %s", e)
        return False
