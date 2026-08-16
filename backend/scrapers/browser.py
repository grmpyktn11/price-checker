import asyncio
import logging
import os
import random
import time

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

# playwright's chromium reports itself as HeadlessChrome; keep the version in step with it
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
# tall viewport: Best Buy only hydrates the search tiles that are in view, and scrolling
# would be one more action. 2400px puts about five tiles on screen.
VIEWPORT = {"width": 1366, "height": 2400}
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1") != "0"   # set 0 locally to watch the browser
MIN_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 3.0
NAV_TIMEOUT_MS = 20000
SELECTOR_TIMEOUT_MS = 10000
MIN_REAL_PAGE_CHARS = 2000   # a challenge/interstitial page is tiny compared to a real one
CACHE_SECONDS = 60

# get_specs and get_reviews load the same product page seconds apart: fetching it twice
# doubles the cost and is itself a bot signal. size one, no eviction policy.
_LAST_PRODUCT_PAGE = {"url": None, "html": None, "fetched_at": 0.0}

logger = logging.getLogger(__name__)


# one browser instance per call, closed immediately after, per the spec
async def fetch_html(url: str, wait_for: str | None = None) -> str:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS)
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT, viewport=VIEWPORT, locale="en-US"
            )
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except PlaywrightError as error:
                # a bot filter can reset the connection before any html arrives. empty html
                # is what looks_blocked() reads, so the caller reports it as a block
                logger.info("navigation failed on %s: %s", url, error)
                return ""
            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=SELECTOR_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    # not fatal: the caller still needs the html to tell a block from a
                    # broken selector
                    logger.warning("%s did not appear on %s", wait_for, url)
            # after navigation so lazy content settles and the request pattern is not machine-regular
            await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
            return await page.content()
        finally:
            await browser.close()


# reuse only the same url, only within the cache window
async def fetch_product_html(url: str) -> str:
    fresh = time.monotonic() - _LAST_PRODUCT_PAGE["fetched_at"] < CACHE_SECONDS
    if _LAST_PRODUCT_PAGE["url"] == url and fresh:
        return _LAST_PRODUCT_PAGE["html"]
    html = await fetch_html(url)
    _LAST_PRODUCT_PAGE.update({"url": url, "html": html, "fetched_at": time.monotonic()})
    return html


# a challenge page is either tiny or carries one of the retailer's block strings
def looks_blocked(html: str, markers: tuple[str, ...]) -> bool:
    if len(html) < MIN_REAL_PAGE_CHARS:
        return True
    lowered = html.lower()
    return any(marker in lowered for marker in markers)
