import pytest
from unittest.mock import MagicMock, patch
from ingestion.geocoder import is_within_toronto, make_geocode_fn, geocode_address


# --- is_within_toronto ---

def test_within_toronto_valid():
    assert is_within_toronto(43.7115, -79.4317) is True

def test_outside_toronto_south():
    assert is_within_toronto(43.10, -79.4317) is False

def test_outside_toronto_west():
    assert is_within_toronto(43.7115, -80.00) is False

def test_boundary_min():
    assert is_within_toronto(43.58, -79.64) is True

def test_boundary_max():
    assert is_within_toronto(43.86, -79.11) is True


# --- make_geocode_fn with mock geolocator ---

def make_mock_geolocator(lat, lng):
    loc = MagicMock()
    loc.latitude = lat
    loc.longitude = lng
    geolocator = MagicMock()
    geolocator.geocode.return_value = loc
    return geolocator


def test_make_geocode_fn_returns_coords():
    geo = make_mock_geolocator(43.7115, -79.4317)
    fn = make_geocode_fn(geolocator=geo)
    result = fn("Bathurst St & Prue Ave, Toronto, ON")
    assert result == (43.7115, -79.4317)


def test_make_geocode_fn_outside_toronto_returns_none():
    geo = make_mock_geolocator(43.10, -79.4317)
    fn = make_geocode_fn(geolocator=geo)
    result = fn("Some address outside Toronto")
    assert result is None


def test_make_geocode_fn_empty_address():
    geo = make_mock_geolocator(43.7115, -79.4317)
    fn = make_geocode_fn(geolocator=geo)
    assert fn("") is None
    assert fn(None) is None


def test_make_geocode_fn_geocoder_returns_none():
    geolocator = MagicMock()
    geolocator.geocode.return_value = None
    fn = make_geocode_fn(geolocator=geolocator)
    assert fn("Unknown address") is None


def test_make_geocode_fn_geocoder_raises():
    geolocator = MagicMock()
    geolocator.geocode.side_effect = Exception("service down")
    fn = make_geocode_fn(geolocator=geolocator)
    assert fn("Bathurst St") is None


# --- geocode_address (network mocked) ---

def test_geocode_address_empty_returns_none():
    assert geocode_address("") is None

def test_geocode_address_network_failure_does_not_raise():
    """On network failure, geocode_address falls back to demo_geocode — never raises."""
    with patch("ingestion.geocoder._geolocator.geocode", side_effect=Exception("timeout")):
        with patch("ingestion.geocoder._disk_cache", {}):
            result = geocode_address("Bathurst St & Prue Ave")
    # demo_geocode may return coords for a known street, or None for unknown — both are valid
    assert result is None or (isinstance(result, tuple) and len(result) == 2)


def test_geocode_address_network_failure_unknown_address_returns_none():
    """On network failure with an address not in STREET_COORDS, returns None."""
    with patch("ingestion.geocoder._geolocator.geocode", side_effect=Exception("timeout")):
        with patch("ingestion.geocoder._disk_cache", {}):
            result = geocode_address("ZZZ Nonexistent Place XYZ 999")
    assert result is None

def test_geocode_address_outside_toronto_returns_none():
    mock_loc = MagicMock()
    mock_loc.latitude = 43.10
    mock_loc.longitude = -79.43
    with patch("ingestion.geocoder._geolocator.geocode", return_value=mock_loc):
        with patch("ingestion.geocoder._disk_cache", {}):
            with patch("ingestion.geocoder.time.sleep"):
                result = geocode_address("Some far away place")
    assert result is None

def test_geocode_address_valid_toronto_location():
    mock_loc = MagicMock()
    mock_loc.latitude = 43.7115
    mock_loc.longitude = -79.4317
    with patch("ingestion.geocoder._geolocator.geocode", return_value=mock_loc):
        with patch("ingestion.geocoder._disk_cache", {}):
            with patch("ingestion.geocoder.time.sleep"):
                with patch("ingestion.geocoder._save_disk_cache"):
                    result = geocode_address("Bathurst St & Prue Ave, Toronto, ON")
    assert result == (43.7115, -79.4317)
