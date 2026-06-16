"""
One-time civic context cache builder — hospitals, schools, neighbourhood population.

Downloads three Toronto Open Data sources and writes a flat cache to civic_cache/:
  hospitals.json      [{name, lat, lng}, ...]
  schools.json        [{name, lat, lng}, ...]
  neighbourhoods.json [{name, lat, lng, population}, ...]

The neighbourhood profile CSV carries population but no geometry. Centroids for
that file come from a small hardcoded table of well-known Toronto neighbourhoods
(same approach as config.py:STREET_COORDS) — coverage is best-effort, not exhaustive.
Neighbourhoods with no centroid match are skipped and counted in the summary.

Existing cache files are skipped unless --force is passed. Network failures fall
back to hardcoded data (hospitals only) or an empty result — never crash.

Usage:
    python3 -m scripts.download_civic_data
    python3 -m scripts.download_civic_data --force
"""
import argparse
import csv
import io
import json
import logging
import re
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CIVIC_CACHE_DIR = Path(__file__).parent.parent / "civic_cache"

HOSPITALS_URL = (
    "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/hospitals/"
    "resource/a7c481d4-b8a0-4ffc-bde0-88c1e2b8f5e3/download/Hospitals.geojson"
)
SCHOOLS_URL = (
    "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/"
    "toronto-district-school-board-tdsb-school-locations/"
    "resource/1eb80b3e-fd06-4b8b-9853-e330b1716aa8/download/tdsb_school_locations.csv"
)
NEIGHBOURHOODS_URL = (
    "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/neighbourhood-profiles/"
    "resource/531ca7d3-07c2-4cc2-8e7f-48af1e0a3c25/download/"
    "neighbourhood-profiles-2021-158-model.csv"
)

# Fallback used when the hospitals download fails or returns bad data.
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

# Best-effort centroids for the neighbourhood profile CSV, which has no geometry
# of its own. Not exhaustive — unmatched neighbourhoods are skipped, not guessed.
NEIGHBOURHOOD_CENTROIDS = {
    "waterfront communities-the island": (43.6390, -79.3780),
    "bay street corridor": (43.6580, -79.3850),
    "church-yonge corridor": (43.6630, -79.3800),
    "niagara": (43.6390, -79.4080),
    "trinity-bellwoods": (43.6470, -79.4180),
    "little portugal": (43.6450, -79.4310),
    "south parkdale": (43.6390, -79.4360),
    "high park-swansea": (43.6500, -79.4720),
    "annex": (43.6700, -79.4040),
    "yonge-eglinton": (43.7050, -79.4030),
    "yonge-st.clair": (43.6880, -79.3970),
    "rosedale-moore park": (43.6800, -79.3780),
    "north riverdale": (43.6680, -79.3500),
    "south riverdale": (43.6620, -79.3460),
    "danforth": (43.6840, -79.3300),
    "east end-danforth": (43.6840, -79.3030),
    "kensington-chinatown": (43.6540, -79.4000),
    "dovercourt-wallace emerson-junction": (43.6660, -79.4430),
    "weston-pellam park": (43.6900, -79.4640),
    "humewood-cedarvale": (43.6850, -79.4280),
    "forest hill south": (43.6940, -79.4140),
    "casa loma": (43.6780, -79.4090),
    "regent park": (43.6600, -79.3640),
    "moss park": (43.6560, -79.3680),
    "cabbagetown-south st.james town": (43.6660, -79.3680),
    "north st.james town": (43.6680, -79.3760),
    "west humber-clairville": (43.7170, -79.5980),
    "mount olive-silverstone-jamestown": (43.7530, -79.5900),
    "humbermede": (43.7400, -79.5470),
    "downsview-roding-cfb": (43.7400, -79.4800),
    "humber summit": (43.7570, -79.5460),
    "york university heights": (43.7730, -79.5040),
    "willowdale east": (43.7700, -79.4070),
    "willowdale west": (43.7650, -79.4280),
    "bayview village": (43.7690, -79.3830),
    "newtonbrook east": (43.7900, -79.4090),
    "bridle path-sunnybrook-york mills": (43.7330, -79.3760),
    "agincourt north": (43.8000, -79.2700),
    "agincourt south-malvern west": (43.7900, -79.2700),
    "milliken": (43.8190, -79.2790),
    "malvern": (43.8090, -79.2240),
    "rouge": (43.8090, -79.1750),
    "scarborough village": (43.7280, -79.2120),
    "woburn": (43.7600, -79.2280),
    "morningside": (43.7860, -79.2120),
    "west hill": (43.7780, -79.1700),
    "birchcliffe-cliffside": (43.6940, -79.2620),
    "cliffcrest": (43.7150, -79.2380),
    "kennedy park": (43.7280, -79.2600),
    "ionview": (43.7320, -79.2680),
    "eglinton east": (43.7300, -79.2300),
    "etobicoke west mall": (43.6480, -79.5700),
    "islington-city centre west": (43.6440, -79.5290),
    "kingsway south": (43.6500, -79.5050),
    "stonegate-queensway": (43.6300, -79.5050),
    "long branch": (43.5950, -79.5430),
    "alderwood": (43.6020, -79.5450),
    "new toronto": (43.6010, -79.5070),
    "mimico": (43.6160, -79.4940),
    "edenbridge-humber valley": (43.6700, -79.5260),
}


def _fetch_json(url: str) -> dict | None:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Fetch failed for %s: %s", url, e)
        return None


def _fetch_text(url: str) -> str | None:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning("Fetch failed for %s: %s", url, e)
        return None


def build_hospitals() -> list[dict]:
    data = _fetch_json(HOSPITALS_URL)
    if not data:
        logger.warning("Hospitals download failed — using hardcoded fallback (%d entries)",
                       len(TORONTO_HOSPITALS))
        return list(TORONTO_HOSPITALS)
    result = []
    try:
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            geom = feature.get("geometry", {})
            coords = geom.get("coordinates")
            name = props.get("NAME")
            if not name or not coords or len(coords) < 2:
                continue
            lng, lat = coords[0], coords[1]
            result.append({"name": name, "lat": lat, "lng": lng})
    except Exception as e:
        logger.warning("Failed to parse hospitals geojson: %s — using hardcoded fallback", e)
        return list(TORONTO_HOSPITALS)
    if not result:
        logger.warning("Hospitals geojson parsed but empty — using hardcoded fallback")
        return list(TORONTO_HOSPITALS)
    return result


def build_schools() -> list[dict]:
    text = _fetch_text(SCHOOLS_URL)
    if not text:
        logger.warning("Schools download failed — no fallback available, schools.json will be empty")
        return []
    result = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            name = row.get("SCHOOL_NAME")
            lat = row.get("LATITUDE")
            lng = row.get("LONGITUDE")
            if not name or not lat or not lng:
                continue
            result.append({"name": name, "lat": float(lat), "lng": float(lng)})
    except Exception as e:
        logger.warning("Failed to parse schools CSV: %s", e)
        return []
    return result


def _match_centroid(column_name: str) -> tuple[float, float] | None:
    """Strip a trailing neighbourhood ID like ' (97)' and match the centroid table."""
    stripped = re.sub(r"\s*\(\d+\)\s*$", "", column_name).strip().lower()
    return NEIGHBOURHOOD_CENTROIDS.get(stripped)


def build_neighbourhoods() -> list[dict]:
    text = _fetch_text(NEIGHBOURHOODS_URL)
    if not text:
        logger.warning("Neighbourhoods download failed — no fallback available, "
                       "neighbourhoods.json will be empty")
        return []

    try:
        reader = csv.DictReader(io.StringIO(text))
        pop_row = None
        for row in reader:
            characteristic = (row.get("Characteristic") or "").strip().lower()
            if characteristic == "population, 2021":
                pop_row = row
                break
    except Exception as e:
        logger.warning("Failed to parse neighbourhood CSV: %s", e)
        return []

    if not pop_row:
        logger.warning("Could not find 'Population, 2021' row in neighbourhood CSV")
        return []

    skip_cols = {"_id", "Category", "Topic", "Data Source", "Characteristic", "City of Toronto"}
    result = []
    skipped = 0
    for col, value in pop_row.items():
        if not col or col in skip_cols:
            continue
        centroid = _match_centroid(col)
        if not centroid:
            skipped += 1
            continue
        try:
            population = int(str(value).replace(",", "").strip())
        except (ValueError, TypeError):
            continue
        lat, lng = centroid
        result.append({"name": col, "lat": lat, "lng": lng, "population": population})

    if skipped:
        logger.info("Skipped %d neighbourhoods with no centroid match", skipped)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and cache civic context data")
    parser.add_argument("--force", action="store_true", help="Re-download even if cache exists")
    args = parser.parse_args()

    CIVIC_CACHE_DIR.mkdir(exist_ok=True)
    print(f"Cache directory: {CIVIC_CACHE_DIR}")

    targets = [
        ("hospitals.json", build_hospitals),
        ("schools.json", build_schools),
        ("neighbourhoods.json", build_neighbourhoods),
    ]
    for filename, builder in targets:
        path = CIVIC_CACHE_DIR / filename
        if not args.force and path.exists():
            print(f"{filename}: cache already exists, skipping ({path})")
            continue
        print(f"Downloading {filename} …")
        data = builder()
        path.write_text(json.dumps(data, indent=2))
        print(f"{filename}: {len(data)} entries cached")


if __name__ == "__main__":
    main()
