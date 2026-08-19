import logging
from urllib.parse import urlparse

from backend.scrapers.browser import fetch_html, page_text

# claude.ai mints these when the person clicks Share on a chat. the page is a public snapshot
# of the messages, readable without logging in, and the person can revoke it by setting the
# chat back to private. this is the only supported way to read a conversation from outside:
# the Claude API has no conversations endpoint and no consumer OAuth
ALLOWED_HOSTS = ("claude.ai", "www.claude.ai")
# the transcript is client-rendered, so wait for message text rather than for the shell
WAIT_SELECTOR = "main"
# measured 2026-08-19: claude.ai sits behind Cloudflare, which serves a headless browser an
# interstitial instead of the page. that challenge is ~370 characters of text, so a low
# threshold let it through to the model, which then truthfully reported no products in it and
# the person was told their conversation had nothing to buy. a real transcript is thousands
MIN_TEXT_CHARS = 1500
# the Cloudflare interstitial, and the sign-in wall a private link redirects to
CHALLENGE_MARKERS = ("performing security verification", "just a moment",
                     "enable javascript and cookies", "verify you are human",
                     "checking your browser", "cf-browser-verification")
SIGN_IN_MARKERS = ("sign in to continue", "log in to claude", "create an account")

logger = logging.getLogger(__name__)


# the url comes from the browser and is handed to page.goto(), so the host is checked before
# anything is fetched. without this, "import from a link" is an open proxy into the private
# network - the same hole already fixed once in the retailer scrapers
def is_share_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and parsed.netloc.lower() in ALLOWED_HOSTS


class NotAShareUrl(ValueError):
    pass


class ShareUnreadable(RuntimeError):
    pass


# kept apart from ShareUnreadable on purpose: one means the link is wrong or gone, the other
# means we never got to look. telling someone their conversation had nothing to buy when we
# never read it is the worst answer available
class ShareBlocked(RuntimeError):
    pass


def looks_challenged(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def looks_signed_out(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SIGN_IN_MARKERS)


# the visible text of a shared conversation. no attempt is made to split it back into turns:
# the extraction call reads prose perfectly well, and every selector guess here would be one
# more thing to break when the page changes
async def fetch_transcript(url: str) -> str:
    if not is_share_url(url):
        raise NotAShareUrl("that is not a claude.ai share link")
    html = await fetch_html(url, WAIT_SELECTOR)
    text = page_text(html)
    # checked before the length test: the challenge page is short, and reporting it as "that
    # link would not load" would send someone off checking a link that is perfectly fine
    if looks_challenged(text):
        logger.warning("claude share fetch hit a bot challenge: %s", url)
        raise ShareBlocked(
            "claude.ai served a bot check instead of the conversation, so Shopper never "
            "got to read it. Nothing is wrong with your link. Copy the conversation text "
            "and paste it here instead."
        )
    if looks_signed_out(text):
        raise ShareUnreadable(
            "that link asked us to sign in, so the chat is probably still private. "
            "Set it to shared in Claude, or paste the text instead."
        )
    if len(text) < MIN_TEXT_CHARS:
        logger.warning("claude share page had %d chars of text: %s", len(text), url)
        raise ShareUnreadable(
            "that link did not load as a shared conversation. It may be private, "
            "revoked, or not a share link - paste the text instead"
        )
    return text
