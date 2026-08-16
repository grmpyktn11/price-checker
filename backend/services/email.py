import html
import logging
import os

import httpx

# httpx against Resend's REST API instead of the resend sdk: this is a single POST with a
# bearer token, httpx is already a dependency, and the sdk is sync-only while both callers
# (scrape_job's immediate send and digest_job) are async
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
USER_EMAIL = os.getenv("USER_EMAIL", "")
ENDPOINT = "https://api.resend.com/emails"
# Resend's shared sender. without a verified domain Resend only delivers to the account's
# own signup address, which is what USER_EMAIL is
FROM_EMAIL = "Deal Tracker <onboarding@resend.dev>"
TIMEOUT_SECONDS = 10

REASON_LABELS = {
    "target_hit": "Target price hit",
    "price_drop": "Price drop",
    "new_alternative": "New alternative",
}

logger = logging.getLogger(__name__)


def money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


# one alert per row: {item_name, reason, price, target_price, retailer, store_name, url}
def render_row(row: dict) -> str:
    url = row.get("url") or ""
    link = f'<a href="{html.escape(url)}">view</a>' if url else "-"
    where = row.get("store_name") or row.get("retailer") or "-"
    return (
        "<tr>"
        f"<td>{html.escape(row.get('item_name') or '')}</td>"
        f"<td>{REASON_LABELS.get(row.get('reason'), row.get('reason') or '')}</td>"
        f"<td>{money(row.get('price'))}</td>"
        f"<td>{money(row.get('target_price'))}</td>"
        f"<td>{html.escape(str(where))}</td>"
        f"<td>{link}</td>"
        "</tr>"
    )


# plain table, inline attributes only: mail clients strip stylesheets
def render_digest(rows: list[dict]) -> str:
    body = "".join(render_row(row) for row in rows)
    return (
        "<html><body>"
        "<h2>Deal Tracker</h2>"
        f"<p>{len(rows)} alert{'s' if len(rows) != 1 else ''}.</p>"
        '<table border="1" cellpadding="6" cellspacing="0">'
        "<tr><th>Item</th><th>Alert</th><th>Price</th><th>Target</th>"
        "<th>Where</th><th>Link</th></tr>"
        f"{body}"
        "</table>"
        "</body></html>"
    )


def digest_subject(rows: list[dict]) -> str:
    return f"Deal Tracker: {len(rows)} alert{'s' if len(rows) != 1 else ''}"


# a target hit goes out on its own, so the subject names the item
def immediate_subject(row: dict) -> str:
    return f"Deal Tracker: target price hit on {row.get('item_name') or 'a watched item'}"


# no key or no recipient configured: render still happened, nothing is sent. False means the
# caller must leave sent_at null so the alert is retried in the next digest
async def send_email(subject: str, html_body: str) -> bool:
    if not RESEND_API_KEY or not USER_EMAIL:
        logger.info("email not configured, skipping send: %s", subject)
        return False
    payload = {"from": FROM_EMAIL, "to": [USER_EMAIL], "subject": subject, "html": html_body}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json=payload,
            )
        if response.status_code >= 400:
            logger.warning("resend rejected the send: %s %s", response.status_code, response.text)
            return False
    except httpx.HTTPError as error:
        logger.warning("resend send failed: %s", error)
        return False
    logger.info("sent email: %s", subject)
    return True
