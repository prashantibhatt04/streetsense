"""
Civic context lookups — hospitals, schools, neighbourhood population.

Reads from civic_cache/ (built once via scripts/download_civic_data.py). Never
makes network calls at runtime and never raises — if the cache is missing,
hospitals/schools/neighbourhoods fall back to small hardcoded lists.
"""
import json
import logging
from pathlib import Path

from tools.geo_tools import haversine_metres

logger = logging.getLogger(__name__)

CIVIC_CACHE_DIR = Path(__file__).parent.parent / "civic_cache"

# Fallback used when civic_cache/hospitals.json hasn't been built (e.g. fresh
# clone, no network access for scripts/download_civic_data.py).
TORONTO_HOSPITALS = [
    {"name": "St Michael's Hospital", "lat": 43.6529, "lng": -79.3757},
    {"name": "Toronto General Hospital", "lat": 43.6591, "lng": -79.3884},
    {"name": "Mount Sinai Hospital", "lat": 43.6577, "lng": -79.3902},
    {"name": "Toronto Western Hospital", "lat": 43.6536, "lng": -79.4102},
    {"name": "Sunnybrook Health Sciences", "lat": 43.7231, "lng": -79.3773},
    {"name": "Humber River Hospital", "lat": 43.7368, "lng": -79.5388},
    {"name": "North York General Hospital", "lat": 43.7701, "lng": -79.4138},
    {"name": "Scarborough Health Network", "lat": 43.7731, "lng": -79.2329},
]

# Fallback used when civic_cache/schools.json hasn't been built.
TORONTO_SCHOOLS_FALLBACK = [
    {"name": "Bathurst Heights Secondary", "lat": 43.7148, "lng": -79.4303},
    {"name": "Vaughan Road Academy", "lat": 43.7050, "lng": -79.4333},
    {"name": "Western Technical-Commercial School", "lat": 43.6511, "lng": -79.4186},
    {"name": "Harbord Collegiate", "lat": 43.6618, "lng": -79.4065},
    {"name": "Central Technical School", "lat": 43.6657, "lng": -79.4030},
    {"name": "Riverdale Collegiate", "lat": 43.6685, "lng": -79.3534},
    {"name": "Runnymede Collegiate", "lat": 43.6510, "lng": -79.4766},
    {"name": "Parkdale Collegiate", "lat": 43.6382, "lng": -79.4487},
    {"name": "Humberside Collegiate", "lat": 43.6518, "lng": -79.4668},
    {"name": "Malvern Collegiate", "lat": 43.6919, "lng": -79.3008},
    {"name": "Northern Secondary School", "lat": 43.7041, "lng": -79.3976},
    {"name": "Lawrence Park Collegiate", "lat": 43.7247, "lng": -79.3919},
    {"name": "York Mills Collegiate", "lat": 43.7461, "lng": -79.3960},
    {"name": "Don Mills Collegiate", "lat": 43.7408, "lng": -79.3402},
    {"name": "Scarborough Collegiate", "lat": 43.7575, "lng": -79.2680},
]

# Fallback used when civic_cache/neighbourhoods.json hasn't been built.
TORONTO_NEIGHBOURHOODS_FALLBACK = [
    {"name": "Annex", "lat": 43.6691, "lng": -79.4072, "population": 30241},
    {"name": "Bathurst Manor", "lat": 43.7598, "lng": -79.4476, "population": 15420},
    {"name": "Bay Street Corridor", "lat": 43.6598, "lng": -79.3869, "population": 25797},
    {"name": "Cabbagetown", "lat": 43.6622, "lng": -79.3608, "population": 11103},
    {"name": "Church-Yonge Corridor", "lat": 43.6625, "lng": -79.3790, "population": 26430},
    {"name": "Corso Italia", "lat": 43.6730, "lng": -79.4446, "population": 12890},
    {"name": "Danforth", "lat": 43.6854, "lng": -79.3486, "population": 14320},
    {"name": "Dovercourt Village", "lat": 43.6542, "lng": -79.4302, "population": 13560},
    {"name": "Dufferin Grove", "lat": 43.6506, "lng": -79.4330, "population": 12710},
    {"name": "Forest Hill", "lat": 43.7040, "lng": -79.4126, "population": 17820},
    {"name": "Harbord Village", "lat": 43.6616, "lng": -79.4080, "population": 10540},
    {"name": "High Park", "lat": 43.6480, "lng": -79.4637, "population": 31560},
    {"name": "Junction Triangle", "lat": 43.6597, "lng": -79.4558, "population": 9870},
    {"name": "Kensington Market", "lat": 43.6545, "lng": -79.4006, "population": 9410},
    {"name": "Lawrence Park", "lat": 43.7265, "lng": -79.3944, "population": 12890},
    {"name": "Little Portugal", "lat": 43.6484, "lng": -79.4345, "population": 10920},
    {"name": "Moss Park", "lat": 43.6542, "lng": -79.3688, "population": 14750},
    {"name": "Mount Pleasant", "lat": 43.6985, "lng": -79.3818, "population": 22340},
    {"name": "Niagara", "lat": 43.6398, "lng": -79.4068, "population": 15640},
    {"name": "North Riverdale", "lat": 43.6771, "lng": -79.3488, "population": 13420},
    {"name": "Palmerston", "lat": 43.6622, "lng": -79.4143, "population": 11830},
    {"name": "Parkdale", "lat": 43.6412, "lng": -79.4444, "population": 17630},
    {"name": "Roncesvalles", "lat": 43.6486, "lng": -79.4487, "population": 13240},
    {"name": "Rosedale", "lat": 43.6823, "lng": -79.3822, "population": 11490},
    {"name": "South Parkdale", "lat": 43.6378, "lng": -79.4398, "population": 21430},
    {"name": "Trinity Bellwoods", "lat": 43.6487, "lng": -79.4174, "population": 18640},
    {"name": "University", "lat": 43.6608, "lng": -79.3988, "population": 8930},
    {"name": "Wallace Emerson", "lat": 43.6570, "lng": -79.4511, "population": 14290},
    {"name": "Weston", "lat": 43.7027, "lng": -79.5175, "population": 17840},
    {"name": "Wychwood", "lat": 43.6816, "lng": -79.4269, "population": 11250},
]


def _load_cache(filename: str, cache_dir: Path) -> list[dict] | None:
    path = cache_dir / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def hospitals_within_metres(
    lat: float, lng: float, radius_m: float = 800, *, cache_dir: Path = CIVIC_CACHE_DIR,
) -> list[dict]:
    """Return hospitals within radius_m of (lat, lng), sorted nearest-first.
    Falls back to a hardcoded list of major Toronto hospitals if the cache
    hasn't been built. Never raises."""
    try:
        hospitals = _load_cache("hospitals.json", cache_dir)
        if not hospitals:
            hospitals = TORONTO_HOSPITALS
        results = []
        for h in hospitals:
            dist = haversine_metres(lat, lng, h["lat"], h["lng"])
            if dist <= radius_m:
                results.append({"name": h["name"], "distance_m": round(dist, 1)})
        return sorted(results, key=lambda r: r["distance_m"])
    except Exception as e:
        logger.warning("hospitals_within_metres failed: %s", e)
        return []


def schools_within_metres(
    lat: float, lng: float, radius_m: float = 500, *, cache_dir: Path = CIVIC_CACHE_DIR,
) -> list[dict]:
    """Return schools within radius_m of (lat, lng), sorted nearest-first.
    Falls back to a hardcoded list of Toronto secondary schools if the cache
    hasn't been built. Never raises."""
    try:
        schools = _load_cache("schools.json", cache_dir)
        if not schools:
            schools = TORONTO_SCHOOLS_FALLBACK
        results = []
        for s in schools:
            dist = haversine_metres(lat, lng, s["lat"], s["lng"])
            if dist <= radius_m:
                results.append({"name": s["name"], "distance_m": round(dist, 1)})
        return sorted(results, key=lambda r: r["distance_m"])
    except Exception as e:
        logger.warning("schools_within_metres failed: %s", e)
        return []


def neighbourhood_population(
    lat: float, lng: float, *, cache_dir: Path = CIVIC_CACHE_DIR,
) -> int:
    """Return the population of the nearest-centroid neighbourhood to (lat, lng).
    Falls back to a hardcoded list of Toronto neighbourhood centroids if the
    cache hasn't been built. Never raises."""
    try:
        neighbourhoods = _load_cache("neighbourhoods.json", cache_dir)
        if not neighbourhoods:
            neighbourhoods = TORONTO_NEIGHBOURHOODS_FALLBACK
        nearest = min(
            neighbourhoods,
            key=lambda n: haversine_metres(lat, lng, n["lat"], n["lng"]),
        )
        return int(nearest.get("population", 0))
    except Exception as e:
        logger.warning("neighbourhood_population failed: %s", e)
        return 0
