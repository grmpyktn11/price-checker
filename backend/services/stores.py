import logging
import os
from math import asin, cos, radians, sin, sqrt

import httpx

# Places (New) text search. the old Places API is retired, so the key/field mask are headers
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
FIELD_MASK = "places.displayName,places.location,places.formattedAddress"
# retailer -> what to type into Places. Amazon is absent on purpose: it has no stores, so it
# is never looked up and its candidates score the online case
STORE_QUERIES = {"target": "Target", "bestbuy": "Best Buy"}
SEARCH_RADIUS_METERS = 40000.0   # ~25 miles, the default profile radius
MAX_RESULTS = 10
TIMEOUT_SECONDS = 10
EARTH_RADIUS_MILES = 3958.8
# the profile location changes almost never, so one lookup serves the process. rounded to
# ~1km so a re-geocode of the same address does not spend two more Places calls
CACHE_DECIMALS = 2

logger = logging.getLogger(__name__)

# lat/lon -> {retailer: nearest_distance_miles}. plain dict, process lifetime, no eviction
_nearest_cache: dict[tuple[float, float], dict[str, float]] = {}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * asin(sqrt(a))


# pure: nearest returned store in miles, or None when the search found nothing usable
def nearest_miles(payload: dict, lat: float, lon: float) -> float | None:
    distances = []
    for place in payload.get("places") or []:
        location = place.get("location") or {}
        if location.get("latitude") is None or location.get("longitude") is None:
            continue
        distances.append(
            haversine_miles(lat, lon, location["latitude"], location["longitude"])
        )
    return min(distances) if distances else None


# one text search biased to the profile location. {} on any failure: a Places outage must
# never raise into the pipeline
async def search_places(client: httpx.AsyncClient, query: str, lat: float, lon: float) -> dict:
    try:
        response = await client.post(
            SEARCH_URL,
            headers={"X-Goog-Api-Key": GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": FIELD_MASK},
            json={
                "textQuery": query,
                "maxResultCount": MAX_RESULTS,
                "locationBias": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lon},
                        "radius": SEARCH_RADIUS_METERS,
                    }
                },
            },
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("places search failed for %r: %s", query, error)
        return {}


# {retailer: nearest_distance_miles} for the retailers that have physical stores. this is
# per-retailer, not per-product: no retailer tells us which store stocks which product, and
# the question the user actually has is "is there one near me to pick something up from".
# a retailer with no store nearby is simply absent from the dict
async def nearest_stores(lat: float, lon: float) -> dict[str, float]:
    key = (round(lat, CACHE_DECIMALS), round(lon, CACHE_DECIMALS))
    if key in _nearest_cache:
        return _nearest_cache[key]
    if not GOOGLE_PLACES_API_KEY:
        logger.warning("no GOOGLE_PLACES_API_KEY, distance scoring stays neutral")
        return {}

    found = {}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        for retailer, query in STORE_QUERIES.items():
            payload = await search_places(client, query, lat, lon)
            # an empty payload is a failed call, not "no store": do not cache a guess
            if not payload:
                return found
            distance = nearest_miles(payload, lat, lon)
            if distance is not None:
                found[retailer] = distance
    _nearest_cache[key] = found
    logger.info("nearest stores for %s: %s", key, found)
    return found
