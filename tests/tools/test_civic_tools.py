import json
from pathlib import Path

from tools.civic_tools import (
    hospitals_within_metres,
    schools_within_metres,
    neighbourhood_population,
    TORONTO_HOSPITALS,
    TORONTO_SCHOOLS_FALLBACK,
    TORONTO_NEIGHBOURHOODS_FALLBACK,
)


def _write_cache(tmp_path: Path, filename: str, data: list[dict]) -> None:
    (tmp_path / filename).write_text(json.dumps(data))


# Bathurst/Prue area, matching the oct2024_bathurst demo scenario
EVENT_LAT, EVENT_LNG = 43.7115, -79.4317


# ---------------------------------------------------------------------------
# hospitals_within_metres
# ---------------------------------------------------------------------------

def test_hospitals_within_metres_returns_results_when_in_range(tmp_path):
    hospitals = [
        {"name": "Nearby Hospital", "lat": 43.7120, "lng": -79.4320},
        {"name": "Far Hospital", "lat": 43.6000, "lng": -79.6000},
    ]
    _write_cache(tmp_path, "hospitals.json", hospitals)
    results = hospitals_within_metres(EVENT_LAT, EVENT_LNG, radius_m=1000, cache_dir=tmp_path)
    names = [r["name"] for r in results]
    assert "Nearby Hospital" in names
    assert "Far Hospital" not in names
    assert all("distance_m" in r for r in results)


def test_hospitals_within_metres_returns_empty_when_none_in_range(tmp_path):
    hospitals = [{"name": "Far Hospital", "lat": 43.6000, "lng": -79.6000}]
    _write_cache(tmp_path, "hospitals.json", hospitals)
    results = hospitals_within_metres(EVENT_LAT, EVENT_LNG, radius_m=500, cache_dir=tmp_path)
    assert results == []


def test_hospitals_within_metres_falls_back_when_cache_missing(tmp_path):
    """No civic_cache/ at all — must use TORONTO_HOSPITALS fallback, never raise."""
    results = hospitals_within_metres(EVENT_LAT, EVENT_LNG, radius_m=50_000, cache_dir=tmp_path)
    assert len(results) > 0
    assert len(results) <= len(TORONTO_HOSPITALS)


# ---------------------------------------------------------------------------
# schools_within_metres
# ---------------------------------------------------------------------------

def test_schools_within_metres_returns_results_when_in_range(tmp_path):
    schools = [
        {"name": "Nearby School", "lat": 43.7118, "lng": -79.4319},
        {"name": "Far School", "lat": 43.6000, "lng": -79.6000},
    ]
    _write_cache(tmp_path, "schools.json", schools)
    results = schools_within_metres(EVENT_LAT, EVENT_LNG, radius_m=500, cache_dir=tmp_path)
    names = [r["name"] for r in results]
    assert "Nearby School" in names
    assert "Far School" not in names


def test_schools_within_metres_returns_empty_outside_radius(tmp_path):
    schools = [{"name": "Far School", "lat": 43.6000, "lng": -79.6000}]
    _write_cache(tmp_path, "schools.json", schools)
    results = schools_within_metres(EVENT_LAT, EVENT_LNG, radius_m=500, cache_dir=tmp_path)
    assert results == []


def test_schools_within_metres_falls_back_when_cache_missing(tmp_path):
    """No civic_cache/ at all — must use TORONTO_SCHOOLS_FALLBACK, never raise."""
    results = schools_within_metres(EVENT_LAT, EVENT_LNG, radius_m=50_000, cache_dir=tmp_path)
    assert len(results) > 0
    assert len(results) <= len(TORONTO_SCHOOLS_FALLBACK)


# ---------------------------------------------------------------------------
# neighbourhood_population
# ---------------------------------------------------------------------------

def test_neighbourhood_population_returns_positive_int_for_known_coordinate(tmp_path):
    neighbourhoods = [
        {"name": "Bathurst Corridor", "lat": 43.7115, "lng": -79.4317, "population": 45000},
        {"name": "Far Neighbourhood", "lat": 43.6000, "lng": -79.6000, "population": 10000},
    ]
    _write_cache(tmp_path, "neighbourhoods.json", neighbourhoods)
    pop = neighbourhood_population(EVENT_LAT, EVENT_LNG, cache_dir=tmp_path)
    assert pop == 45000


def test_neighbourhood_population_falls_back_when_cache_missing(tmp_path):
    """No civic_cache/ at all — must use TORONTO_NEIGHBOURHOODS_FALLBACK, never raise."""
    pop = neighbourhood_population(EVENT_LAT, EVENT_LNG, cache_dir=tmp_path)
    assert pop > 0
    assert pop in {n["population"] for n in TORONTO_NEIGHBOURHOODS_FALLBACK}
