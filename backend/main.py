import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

# must run before any module that reads env vars at import time (db.py DATABASE_URL, bestbuy.py key)
load_dotenv()

# httpx logs the full request url at INFO, and our api keys travel in query strings,
# so leaving it on writes plaintext keys into the server log
logging.getLogger("httpx").setLevel(logging.WARNING)

from backend.db import Base, engine  # noqa: E402
from backend import models  # noqa: E402,F401  imported so create_all sees every table
from backend.routers import alerts, chat, items, listings, profile  # noqa: E402
from backend.scheduler import start_scheduler  # noqa: E402


# jobs run in the same process as the API. lifespan, not import time, so importing the app
# (tests, scripts) never starts a background thread
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield


app = FastAPI(title="Deal Tracker", lifespan=lifespan)

# single user, single process, no migrations
Base.metadata.create_all(bind=engine)

app.include_router(profile.router)
app.include_router(chat.router)
app.include_router(items.router)
app.include_router(listings.router)
app.include_router(alerts.router)
