import html
import logging
import os

import httpx

# httpx against Resend's REST API instead of the resend sdk: this is a single POST with a
# bearer token, httpx is already a dependency, and the sdk is sync-only while both callers
# (scrape_job's immediate send and digest_job) are async
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
# the fallback only. the address is set in Settings and stored on the profile row; this keeps
# an install that never opened Settings working
USER_EMAIL = os.getenv("USER_EMAIL", "")
ENDPOINT = "https://api.resend.com/emails"
# Resend's shared sender. without a verified domain Resend only delivers to the account's
# own signup address, which is what USER_EMAIL is
FROM_EMAIL = "Shopper <onboarding@resend.dev>"
TIMEOUT_SECONDS = 10

REASON_LABELS = {
    "target_hit": "Target price hit",
    "price_drop": "Price drop",
    "new_alternative": "New alternative",
}

logger = logging.getLogger(__name__)


def money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


# the app's palette, hard-coded rather than read from styles.css: an email is a separate
# document and mail clients strip stylesheets, so every colour here is written inline below
INK = "#16281b"
MUTED = "#5b6b5f"
BORDER = "#cfe0d2"
BUTTER = "#f8e6a8"
CARD = "#ffffff"
PAGE = "#f6f7ec"
GREEN = "#2c7a3f"
# the reason chip's fill. a target hit is the one worth spotting from across the room
REASON_TINT = {
    "target_hit": "#f8e6a8",
    "price_drop": "#d8ecdc",
    "new_alternative": "#e5e7f5",
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


# one card per alert. tables, not divs: Outlook renders flexbox as a single column and gmail
# strips anything in a <style> block, so every rule is an inline attribute
def render_row(row: dict) -> str:
    url = row.get("url") or ""
    reason = row.get("reason") or ""
    label = REASON_LABELS.get(reason, reason)
    tint = REASON_TINT.get(reason, BUTTER)
    where = row.get("store_name") or row.get("retailer") or "-"
    target = row.get("target_price")
    button = (
        f'<a href="{html.escape(url)}" style="display:inline-block;background:{GREEN};'
        f'color:#ffffff;font-weight:700;font-size:14px;text-decoration:none;'
        f'padding:10px 18px;border-radius:999px">View it</a>'
        if url else ""
    )
    return (
        f'<tr><td style="padding:0 0 14px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{CARD};border:2px solid {BORDER};border-radius:16px">'
        f'<tr><td style="padding:18px 20px">'
        f'<span style="display:inline-block;background:{tint};color:{INK};font-size:12px;'
        f'font-weight:700;padding:4px 10px;border-radius:999px">{html.escape(str(label))}</span>'
        f'<p style="margin:10px 0 2px;font-size:18px;font-weight:700;color:{INK}">'
        f'{html.escape(row.get("item_name") or "")}</p>'
        f'<p style="margin:0 0 12px;font-size:14px;color:{MUTED}">'
        f'{money(row.get("price"))} at {html.escape(str(where))}'
        + (f' &middot; target {money(target)}' if target is not None else "")
        + f'</p>{button}'
        f'</td></tr></table></td></tr>'
    )


# the whole message. width capped at 560 because gmail on a phone is about that wide
def render_digest(rows: list[dict]) -> str:
    body = "".join(render_row(row) for row in rows)
    count = f"{len(rows)} alert{'s' if len(rows) != 1 else ''}"
    return (
        f'<div style="margin:0;padding:24px 12px;background:{PAGE};font-family:{FONT}">'
        f'<table role="presentation" align="center" width="560" cellpadding="0" cellspacing="0" '
        f'style="width:100%;max-width:560px">'
        f'<tr><td style="padding:0 4px 18px">'
        f'<span style="font-size:26px;font-weight:800;color:{INK};letter-spacing:-0.5px">'
        f'shopper</span>'
        f'<span style="font-size:14px;color:{MUTED}"> &nbsp;{count}</span>'
        f'</td></tr>'
        f"{body}"
        f'<tr><td style="padding:8px 4px 0;font-size:12px;color:{MUTED}">'
        f'Prices were correct when Shopper checked them. Watched items are re-checked '
        f'every six hours.</td></tr>'
        f'</table></div>'
    )


def digest_subject(rows: list[dict]) -> str:
    return f"Shopper: {len(rows)} alert{'s' if len(rows) != 1 else ''}"


# a target hit goes out on its own, so the subject names the item
def immediate_subject(row: dict) -> str:
    return f"Shopper: target price hit on {row.get('item_name') or 'a watched item'}"


# no key or no recipient configured: render still happened, nothing is sent. False means the
# caller must leave sent_at null so the alert is retried in the next digest
async def send_email(subject: str, html_body: str, to: str | None = None) -> bool:
    recipient = to or USER_EMAIL
    if not RESEND_API_KEY or not recipient:
        logger.info("email not configured, skipping send: %s", subject)
        return False
    payload = {"from": FROM_EMAIL, "to": [recipient], "subject": subject, "html": html_body}
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
