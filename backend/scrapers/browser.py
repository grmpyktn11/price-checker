import asyncio
import logging
import os
import random
import sys
import time

from collections import OrderedDict

from bs4 import BeautifulSoup
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
MIN_HYDRATED_TILES = 4       # enough of a virtualized grid to be worth parsing
HYDRATION_POLL_MS = 400      # two polls the same means the grid stopped filling in
# short on purpose: whatever hydrated by now is what we parse, and a slow grid must not
# add the full selector timeout to every search
HYDRATION_TIMEOUT_MS = 4000
CACHE_SECONDS = 60
# a tall viewport over a 2MB retailer page is enough layout to crash the renderer
# ("Target crashed"). the shared-memory and gpu flags are the usual cure
LAUNCH_ARGS = ["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"]

# get_specs, get_reviews and get_page_text load the same product page seconds apart: fetching
# it three times triples the cost and is itself a bot signal. keyed by url and small rather
# than a single slot - with the retailers running concurrently, one slot is thrashed by the
# next retailer's product before the first retailer asks for it again
PRODUCT_PAGE_CACHE_SIZE = 12
_PRODUCT_PAGES: "OrderedDict[str, tuple[str, float]]" = OrderedDict()

logger = logging.getLogger(__name__)


# uvicorn --reload runs on a Windows selector loop, which cannot spawn subprocesses, and
# playwright launches chromium as one. without this every scrape raises NotImplementedError
# in the real app while working fine from a script
def needs_own_loop() -> bool:
    if sys.platform != "win32":
        return False
    return not isinstance(asyncio.get_event_loop(), asyncio.ProactorEventLoop)


# a private proactor loop in a worker thread, so the caller's loop is untouched
def fetch_in_worker(url: str, wait_for: str | None) -> str:
    loop = asyncio.ProactorEventLoop()
    try:
        return loop.run_until_complete(open_page(url, wait_for))
    finally:
        loop.close()


async def fetch_html(url: str, wait_for: str | None = None) -> str:
    if needs_own_loop():
        return await asyncio.to_thread(fetch_in_worker, url, wait_for)
    return await open_page(url, wait_for)


# one browser instance per call, closed immediately after, per the spec
async def open_page(url: str, wait_for: str | None = None) -> str:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS, args=LAUNCH_ARGS)
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
                    # a virtualized grid renders empty tiles first and hydrates them one by
                    # one, so waiting for the first match returns a nearly empty page. wait
                    # for the count to stop growing instead, and settle for whatever it got
                    await page.wait_for_selector(wait_for, timeout=SELECTOR_TIMEOUT_MS)
                    await page.wait_for_function(
                        """([selector, settled]) => {
                            const n = document.querySelectorAll(selector).length;
                            const stable = window.__lastCount === n;
                            window.__lastCount = n;
                            return stable && n >= settled;
                        }""",
                        arg=[wait_for, MIN_HYDRATED_TILES],
                        polling=HYDRATION_POLL_MS,
                        timeout=HYDRATION_TIMEOUT_MS,
                    )
                except PlaywrightTimeoutError:
                    # not fatal: the caller still needs the html to tell a block from a
                    # broken selector, and a short page is better than none
                    logger.warning("%s did not appear on %s", wait_for, url)
            # after navigation so lazy content settles and the request pattern is not machine-regular
            await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
            return await page.content()
        finally:
            await browser.close()


# reuse only the same url, only within the cache window
async def fetch_product_html(url: str) -> str:
    cached = _PRODUCT_PAGES.get(url)
    if cached and time.monotonic() - cached[1] < CACHE_SECONDS:
        _PRODUCT_PAGES.move_to_end(url)
        return cached[0]
    html = await fetch_html(url)
    _PRODUCT_PAGES[url] = (html, time.monotonic())
    _PRODUCT_PAGES.move_to_end(url)
    while len(_PRODUCT_PAGES) > PRODUCT_PAGE_CACHE_SIZE:
        _PRODUCT_PAGES.popitem(last=False)
    return html


# visible text of a page, for the LLM spec fallback when the spec selectors found nothing
def page_text(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


# the retailer's own "no results" wording. this is what tells a genuinely empty result set
# apart from selectors that broke on a real page full of products
def looks_empty(html: str, markers: tuple[str, ...]) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in markers)


# a challenge page is either tiny or carries one of the retailer's block strings
def looks_blocked(html: str, markers: tuple[str, ...]) -> bool:
    if len(html) < MIN_REAL_PAGE_CHARS:
        return True
    lowered = html.lower()
    return any(marker in lowered for marker in markers)
