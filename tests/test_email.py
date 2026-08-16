import asyncio

from backend.services.email import (
    digest_subject,
    immediate_subject,
    money,
    render_digest,
    send_email,
)

ROW = {
    "item_name": "portable charger",
    "reason": "price_drop",
    "price": 89.99,
    "target_price": 99.0,
    "retailer": "bestbuy",
    "store_name": None,
    "url": "https://example.com/p?a=1&b=2",
}


def test_money_formats_and_handles_null():
    assert money(1234.5) == "$1,234.50"
    assert money(None) == "-"


def test_digest_contains_the_alert_fields():
    html = render_digest([ROW])
    assert "portable charger" in html
    assert "Price drop" in html
    assert "$89.99" in html
    assert "bestbuy" in html


def test_digest_escapes_text_and_urls():
    html = render_digest([{**ROW, "item_name": "<script>x</script>"}])
    assert "<script>" not in html
    # & in a query string has to survive as an entity, not raw
    assert "a=1&amp;b=2" in html


def test_missing_url_renders_no_link():
    assert "<a href" not in render_digest([{**ROW, "url": None}])


def test_subjects():
    assert digest_subject([ROW]) == "Deal Tracker: 1 alert"
    assert digest_subject([ROW, ROW]) == "Deal Tracker: 2 alerts"
    assert immediate_subject(ROW) == "Deal Tracker: target price hit on portable charger"


# conftest blanks the key: no request is made and the caller learns nothing was sent
def test_send_without_a_key_returns_false():
    assert asyncio.run(send_email("subject", "<html></html>")) is False
