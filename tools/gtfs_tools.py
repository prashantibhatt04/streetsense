"""
TTC GTFS spatial lookup.

Finds TTC routes that have stops within a given radius of a coordinate pair.
Falls back to an empty list if the cache is not built — the prediction agent
degrades gracefully to keyword matching.

One-time setup (~30s):
    python3 -m scripts.download_gtfs
"""
import csv
import io
import json
import logging
import urllib.request
import zipfile
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

logger = logging.getLogger(__name__)

_CKAN_API = "https://ckan0.cf.opendata.inter.toronto.ca"
_GTFS_PACKAGE_ID = "ttc-routes-and-schedules"
_GTFS_DIRECT_URL = (
    "https://opendata.toronto.ca/toronto.transit.commission/"
    "ttc-routes-and-schedules/OpenData_TTC_Schedules.zip"
)

GTFS_CACHE_DIR = Path(__file__).parent.parent / "gtfs_cache"
_EARTH_R = 6_371_000.0

_ROUTE_TYPE_LABEL = {0: "streetcar", 1: "subway", 3: "bus"}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * _EARTH_R * asin(sqrt(a))


def _fetch_gtfs_url() -> str:
    """
    Return the GTFS zip download URL.
    Tries CKAN API first (canonical); falls back to the known direct URL.
    """
    try:
        api_url = f"{_CKAN_API}/api/3/action/package_show?id={_GTFS_PACKAGE_ID}"
        with urllib.request.urlopen(api_url, timeout=10) as resp:
            pkg = json.loads(resp.read())
        for resource in pkg.get("result", {}).get("resources", []):
            url = resource.get("url", "")
            if resource.get("format", "").upper() == "GTFS" or url.lower().endswith(".zip"):
                return url
    except Exception:
        pass
    logger.info("CKAN unavailable — using direct Toronto Open Data URL")
    return _GTFS_DIRECT_URL


def build_gtfs_cache(force: bool = False, *, cache_dir: Path = GTFS_CACHE_DIR) -> bool:
    """
    Download TTC GTFS and write three cache files:
      routes.json, stops.json, stop_routes.json

    Slow (~30s) — run once. Returns True on success.
    Existing cache is skipped unless force=True.
    """
    routes_f     = cache_dir / "routes.json"
    stops_f      = cache_dir / "stops.json"
    stop_routes_f = cache_dir / "stop_routes.json"

    if not force and routes_f.exists() and stops_f.exists() and stop_routes_f.exists():
        return True

    url = _fetch_gtfs_url()
    logger.info("Downloading TTC GTFS …")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except Exception as exc:
        logger.error("GTFS download failed: %s", exc)
        return False

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # routes.txt
            routes: dict[str, dict] = {}
            with zf.open("routes.txt") as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                    rtype = _ROUTE_TYPE_LABEL.get(int(row.get("route_type") or 3), "bus")
                    routes[row["route_id"]] = {
                        "short_name": row.get("route_short_name") or row["route_id"],
                        "long_name":  row.get("route_long_name") or "",
                        "route_type": rtype,
                    }

            # stops.txt
            stops: dict[str, dict] = {}
            with zf.open("stops.txt") as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                    stops[row["stop_id"]] = {
                        "lat": float(row["stop_lat"]),
                        "lon": float(row["stop_lon"]),
                    }

            # trips.txt → trip_id: route_id
            trip_route: dict[str, str] = {}
            with zf.open("trips.txt") as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                    trip_route[row["trip_id"]] = row["route_id"]

            # stop_times.txt → stop_id: [route_ids]  (slow — streamed)
            logger.info("Building stop→route index …")
            seen: dict[str, set] = {}
            with zf.open("stop_times.txt") as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                    rid = trip_route.get(row["trip_id"])
                    if rid:
                        sid = row["stop_id"]
                        seen.setdefault(sid, set()).add(rid)
            stop_routes = {k: sorted(v) for k, v in seen.items()}

    except Exception as exc:
        logger.error("GTFS parse failed: %s", exc)
        return False

    cache_dir.mkdir(exist_ok=True)
    routes_f.write_text(json.dumps(routes))
    stops_f.write_text(json.dumps(stops))
    stop_routes_f.write_text(json.dumps(stop_routes))
    logger.info("GTFS cache built: %d routes, %d stops", len(routes), len(stops))
    return True


def routes_near_coords(
    lat: float,
    lon: float,
    radius_m: float = 500,
    *,
    cache_dir: Path = GTFS_CACHE_DIR,
) -> list[dict]:
    """
    Return TTC routes that have at least one stop within radius_m of (lat, lon).
    Each item: {"route_id": "28", "short_name": "28", "long_name": "Bayview",
                "route_type": "bus"}
    Returns [] if GTFS cache is absent — run `python3 -m scripts.download_gtfs` first.
    Never raises.
    """
    stops_f       = cache_dir / "stops.json"
    stop_routes_f = cache_dir / "stop_routes.json"
    routes_f      = cache_dir / "routes.json"

    if not (stops_f.exists() and stop_routes_f.exists() and routes_f.exists()):
        return []

    try:
        stops       = json.loads(stops_f.read_text())
        stop_routes = json.loads(stop_routes_f.read_text())
        routes      = json.loads(routes_f.read_text())
    except Exception as exc:
        logger.error("Failed to load GTFS cache: %s", exc)
        return []

    nearby: set[str] = set()
    for sid, s in stops.items():
        if _haversine_m(lat, lon, s["lat"], s["lon"]) <= radius_m:
            for rid in stop_routes.get(sid, []):
                nearby.add(rid)

    result = []
    for rid in sorted(nearby, key=lambda r: routes.get(r, {}).get("short_name", r)):
        r = routes.get(rid)
        if r:
            result.append({"route_id": rid, **r})
    return result
