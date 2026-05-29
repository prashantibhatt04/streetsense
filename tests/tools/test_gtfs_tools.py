import json
import pytest
from math import isclose
from pathlib import Path
from tools.gtfs_tools import _haversine_m, routes_near_coords


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_cache(tmp_path: Path, routes: dict, stops: dict, stop_routes: dict) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "routes.json").write_text(json.dumps(routes))
    (tmp_path / "stops.json").write_text(json.dumps(stops))
    (tmp_path / "stop_routes.json").write_text(json.dumps(stop_routes))


SAMPLE_ROUTES = {
    "28": {"short_name": "28", "long_name": "Bayview", "route_type": "bus"},
    "25": {"short_name": "25", "long_name": "Don Mills", "route_type": "bus"},
    "511": {"short_name": "511", "long_name": "Bathurst", "route_type": "streetcar"},
}

# DVP/Bayview area: event at 43.6592, -79.3551
# stop-A: 43.6600, -79.3555 — 89m away — served by route 28
# stop-B: 43.6610, -79.3560 — 214m away — served by routes 28, 25
# stop-C: 43.7115, -79.4317 — ~8km away — served by route 511 (should be excluded)
SAMPLE_STOPS = {
    "stop-A": {"lat": 43.6600, "lon": -79.3555},
    "stop-B": {"lat": 43.6610, "lon": -79.3560},
    "stop-C": {"lat": 43.7115, "lon": -79.4317},
}

SAMPLE_STOP_ROUTES = {
    "stop-A": ["28"],
    "stop-B": ["28", "25"],
    "stop-C": ["511"],
}


# ---------------------------------------------------------------------------
# _haversine_m
# ---------------------------------------------------------------------------

def test_haversine_same_point():
    assert _haversine_m(43.65, -79.38, 43.65, -79.38) == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance():
    # DVP flooding event to stop-A (~89m)
    d = _haversine_m(43.6592, -79.3551, 43.6600, -79.3555)
    assert 70 < d < 120


def test_haversine_larger_distance():
    # roughly 300m
    d = _haversine_m(43.6592, -79.3551, 43.6619, -79.3551)
    assert 200 < d < 400


# ---------------------------------------------------------------------------
# routes_near_coords — no cache
# ---------------------------------------------------------------------------

def test_returns_empty_when_no_cache(tmp_path):
    result = routes_near_coords(43.6592, -79.3551, cache_dir=tmp_path)
    assert result == []


def test_returns_empty_when_only_partial_cache(tmp_path):
    (tmp_path / "routes.json").write_text(json.dumps(SAMPLE_ROUTES))
    # stops.json and stop_routes.json missing
    result = routes_near_coords(43.6592, -79.3551, cache_dir=tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# routes_near_coords — with mock cache
# ---------------------------------------------------------------------------

def test_finds_nearby_routes(tmp_path):
    write_cache(tmp_path, SAMPLE_ROUTES, SAMPLE_STOPS, SAMPLE_STOP_ROUTES)
    result = routes_near_coords(43.6592, -79.3551, radius_m=500, cache_dir=tmp_path)
    short_names = {r["short_name"] for r in result}
    assert "28" in short_names
    assert "25" in short_names


def test_excludes_distant_routes(tmp_path):
    write_cache(tmp_path, SAMPLE_ROUTES, SAMPLE_STOPS, SAMPLE_STOP_ROUTES)
    result = routes_near_coords(43.6592, -79.3551, radius_m=500, cache_dir=tmp_path)
    short_names = {r["short_name"] for r in result}
    assert "511" not in short_names


def test_smaller_radius_excludes_borderline_stop(tmp_path):
    # stop-B is ~214m away — exclude it with radius=100
    write_cache(tmp_path, SAMPLE_ROUTES, SAMPLE_STOPS, SAMPLE_STOP_ROUTES)
    result = routes_near_coords(43.6592, -79.3551, radius_m=100, cache_dir=tmp_path)
    short_names = {r["short_name"] for r in result}
    assert "28" in short_names   # stop-A is ~89m — included
    assert "25" not in short_names  # only reached via stop-B


def test_result_has_required_fields(tmp_path):
    write_cache(tmp_path, SAMPLE_ROUTES, SAMPLE_STOPS, SAMPLE_STOP_ROUTES)
    result = routes_near_coords(43.6592, -79.3551, radius_m=500, cache_dir=tmp_path)
    for r in result:
        assert "route_id" in r
        assert "short_name" in r
        assert "long_name" in r
        assert "route_type" in r


def test_no_duplicates_when_multiple_nearby_stops_same_route(tmp_path):
    # Both stop-A and stop-B serve route 28
    write_cache(tmp_path, SAMPLE_ROUTES, SAMPLE_STOPS, SAMPLE_STOP_ROUTES)
    result = routes_near_coords(43.6592, -79.3551, radius_m=500, cache_dir=tmp_path)
    route_ids = [r["route_id"] for r in result]
    assert len(route_ids) == len(set(route_ids))


# ---------------------------------------------------------------------------
# routes_near_coords — corrupt cache
# ---------------------------------------------------------------------------

def test_never_raises_on_corrupt_stops(tmp_path):
    (tmp_path / "routes.json").write_text(json.dumps(SAMPLE_ROUTES))
    (tmp_path / "stops.json").write_text("not valid json{{{")
    (tmp_path / "stop_routes.json").write_text(json.dumps(SAMPLE_STOP_ROUTES))
    result = routes_near_coords(43.6592, -79.3551, cache_dir=tmp_path)
    assert result == []


def test_never_raises_on_empty_stops(tmp_path):
    write_cache(tmp_path, SAMPLE_ROUTES, {}, {})
    result = routes_near_coords(43.6592, -79.3551, cache_dir=tmp_path)
    assert result == []
