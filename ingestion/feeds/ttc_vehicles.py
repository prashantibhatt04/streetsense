"""
TTC real-time vehicle positions via Umoiq/NextBus public JSON feed.
Returns positions filtered to a given set of route IDs.
Used by /api/vehicles endpoint to animate moving vehicles on the map.
"""
import logging
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://retro.umoiq.com/service/publicJSONFeed"

# TTC route tags for key streetcar and bus routes
ROUTE_TAGS = {
    "501": "501", "504": "504", "505": "505", "506": "506",
    "510": "510", "511": "511", "512": "512",
}


def fetch_route_vehicles(route_id: str) -> list[dict]:
    """
    Fetch real-time vehicle positions for a single TTC route.
    Returns list of {vehicle_id, route_id, lat, lng, bearing, speed}.
    Returns [] on any failure — never raises.
    """
    route_tag = ROUTE_TAGS.get(route_id, route_id)
    try:
        resp = requests.get(
            BASE_URL,
            params={"command": "vehicleLocations", "a": "ttc", "r": route_tag, "t": "0"},
            timeout=8,
            headers={"User-Agent": "StreetSense/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        raw_vehicles = data.get("vehicle", [])
        if isinstance(raw_vehicles, dict):
            raw_vehicles = [raw_vehicles]  # single vehicle comes as a dict

        results = []
        for v in raw_vehicles:
            try:
                results.append({
                    "vehicle_id": str(v.get("id", "")),
                    "route_id":   str(v.get("routeTag", route_id)),
                    "lat":        float(v.get("lat", 0)),
                    "lng":        float(v.get("lon", 0)),
                    "bearing":    float(v.get("heading", 0)),
                    "speed":      float(v.get("speedKmHr", 0)),
                    "secs_old":   int(v.get("secsSinceReport", 0)),
                })
            except (ValueError, TypeError):
                continue
        return results
    except Exception as e:
        logger.warning("TTC vehicle fetch failed for route %s: %s", route_id, e)
        return []


def fetch_vehicle_positions(route_filter: list[str] | None = None) -> list[dict]:
    """
    Fetch TTC vehicle positions for the given route IDs.
    route_filter: list like ["511", "501"]. If None, fetches all key routes.
    Returns combined list sorted by route_id.
    Returns [] on total failure — never raises.
    """
    routes = route_filter if route_filter else list(ROUTE_TAGS.keys())
    all_vehicles = []
    for route_id in routes:
        all_vehicles.extend(fetch_route_vehicles(route_id))

    logger.info("Fetched %d TTC vehicles for routes %s", len(all_vehicles), routes)
    return all_vehicles
