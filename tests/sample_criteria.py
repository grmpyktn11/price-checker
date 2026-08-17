# a criteria dict in the shape criteria.py (LLM call #1) emits, used by the tests that need
# one without making a model call. lives here, not in backend: nothing in the app ships
# canned criteria any more
SAMPLE_CRITERIA = {
    "name": "portable charger",
    "category": "electronics",
    "keywords": ["usb-c", "140w"],
    "must_haves": [
        {"field": "Battery Capacity", "op": ">=", "value": 20000},
    ],
    "preferred_specs": [
        {"field": "Number of USB Ports", "op": ">=", "value": 3},
        {"field": "Product Weight", "op": "<=", "value": 1.0},
    ],
    "nice_to_haves": ["compact", "looks sleek"],
    "budget_max": 150.0,
    "target_price": 99.0,
    "fulfillment_preference": "either",
    "radius_miles": 25,
    "min_review_count": 5,
}
