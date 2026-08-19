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
# a rendered share page is tens of kilobytes of text; anything this small is a shell, a
# sign-in wall, or a revoked link
MIN_TEXT_CHARS = 200

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


# the visible text of a shared conversation. no attempt is made to split it back into turns:
# the extraction call reads prose perfectly well, and every selector guess here would be one
# more thing to break when the page changes
async def fetch_transcript(url: str) -> str:
    if not is_share_url(url):
        raise NotAShareUrl("that is not a claude.ai share link")
    html = await fetch_html(url, WAIT_SELECTOR)
    text = page_text(html)
    if len(text) < MIN_TEXT_CHARS:
        logger.warning("claude share page had %d chars of text: %s", len(text), url)
        raise ShareUnreadable(
            "that link did not load as a shared conversation. It may be private, "
            "revoked, or not a share link - paste the text instead"
        )
    return text
