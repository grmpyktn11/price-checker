import pytest

from backend.services.ranking import NEUTRAL_SCORE, compute_distance_score
from backend.services.stores import STORE_QUERIES, haversine_miles, nearest_miles

LAT = 37.7749
LON = -122.4194
# the shape Places (New) returns under the field mask the lookup asks for
PAYLOAD = {
    "places": [
        {"displayName": {"text": "Target"}, "location": {"latitude": 37.7849,
                                                         "longitude": -122.4094}},
        {"displayName": {"text": "Target"}, "location": {"latitude": 37.7649,
                                                         "longitude": -122.4194}},
    ]
}


# San Francisco to Los Angeles, about 347 miles
def test_haversine_miles():
    assert haversine_miles(LAT, LON, 34.0522, -118.2437) == pytest.approx(347, abs=5)


def test_haversine_is_zero_at_the_same_point():
    assert haversine_miles(LAT, LON, LAT, LON) == 0.0


def test_nearest_miles_picks_the_closest_store():
    assert nearest_miles(PAYLOAD, LAT, LON) == pytest.approx(0.69, abs=0.05)


def test_nearest_miles_without_results():
    assert nearest_miles({}, LAT, LON) is None
    assert nearest_miles({"places": [{"displayName": {"text": "Target"}}]}, LAT, LON) is None


# amazon has no stores, so it is never looked up and always scores the online case
def test_amazon_is_not_a_store_retailer():
    assert set(STORE_QUERIES) == {"target", "bestbuy"}


# the curve the store distance feeds: nearest is best, neutral when there is no store
@pytest.mark.parametrize(
    "distance,expected",
    [(0.57, 0.977), (1.08, 0.957), (12.5, 0.5), (25.0, 0.0), (40.0, 0.0), (None, NEUTRAL_SCORE)],
)
def test_distance_score_curve(distance, expected):
    assert compute_distance_score(distance, 25) == pytest.approx(expected, abs=1e-3)
