import logging
import os
import time
import json
from pathlib import Path
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from config import TORONTO_BBOX, GEOCODE_DELAY_SECONDS, STREET_COORDS

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent.parent / "geocode_cache.json"

_geolocator = Nominatim(user_agent="StreetSense/1.0 (toronto-hackathon)", timeout=10)


def _load_disk_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_disk_cache(cache: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception as e:
        logger.warning("Could not save geocode cache: %s", e)


_disk_cache: dict = _load_disk_cache()


def is_within_toronto(lat: float, lng: float) -> bool:
    return (
        TORONTO_BBOX["lat_min"] <= lat <= TORONTO_BBOX["lat_max"]
        and TORONTO_BBOX["lng_min"] <= lng <= TORONTO_BBOX["lng_max"]
    )


def demo_geocode(address: str) -> tuple[float, float] | None:
    """
    Fast offline geocoder for demo/fallback use.
    Matches address string against known Toronto street centroids.
    Activated when STREETSENSE_GEOCODER=demo or Nominatim is unavailable.
    """
    if not address:
        return None
    lowered = address.lower()
    for street, coords in STREET_COORDS.items():
        if street in lowered:
            return coords
    return None


def geocode_address(address: str) -> tuple[float, float] | None:
    """
    Geocode an address to (lat, lng).
    Strategy:
      1. Return from JSON disk cache if previously seen.
      2. If STREETSENSE_GEOCODER=demo, use offline street-centroid fallback.
      3. Call Nominatim (rate-limited). On failure, fall back to demo_geocode.
      4. Reject results outside Toronto bounds.
    Returns None if address cannot be geocoded within Toronto.
    Never raises.
    """
    if not address or not address.strip():
        return None

    if address in _disk_cache:
        cached = _disk_cache[address]
        return tuple(cached) if cached else None

    if os.getenv("STREETSENSE_GEOCODER", "nominatim") == "demo":
        return demo_geocode(address)

    query = address if "toronto" in address.lower() else f"{address}, Toronto, ON"

    try:
        time.sleep(GEOCODE_DELAY_SECONDS)
        location = _geolocator.geocode(query)
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.warning("Geocoding failed for '%s': %s — trying demo fallback", address, e)
        return demo_geocode(address)
    except Exception as e:
        logger.warning("Unexpected geocoding error for '%s': %s", address, e)
        return demo_geocode(address)

    if location is None:
        result = demo_geocode(address)
        _disk_cache[address] = list(result) if result else None
        _save_disk_cache(_disk_cache)
        return result

    lat, lng = location.latitude, location.longitude
    if not is_within_toronto(lat, lng):
        result = demo_geocode(address)
        _disk_cache[address] = list(result) if result else None
        _save_disk_cache(_disk_cache)
        return result

    result = (lat, lng)
    _disk_cache[address] = [lat, lng]
    _save_disk_cache(_disk_cache)
    return result


def make_geocode_fn(geolocator=None):
    """
    Returns a geocode callable. If geolocator is None, returns geocode_address.
    If a geolocator object is provided (e.g. a mock), wraps it with Toronto bounds check.
    Used in tests to inject mock geocoders without touching the disk cache.
    """
    if geolocator is None:
        return geocode_address

    def _geocode(address: str) -> tuple[float, float] | None:
        if not address or not address.strip():
            return None
        try:
            location = geolocator.geocode(address)
            if location is None:
                return None
            if not is_within_toronto(location.latitude, location.longitude):
                return None
            return (location.latitude, location.longitude)
        except Exception as e:
            logger.warning("Mock geocoder error: %s", e)
            return None

    return _geocode
