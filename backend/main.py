from dotenv import load_dotenv
from fastapi import FastAPI

# must run before any module that reads env vars at import time (db.py DATABASE_URL, bestbuy.py key)
load_dotenv()

from backend.db import Base, engine  # noqa: E402
from backend import models  # noqa: E402,F401  imported so create_all sees every table
from backend.routers import profile  # noqa: E402

app = FastAPI(title="Deal Tracker")

# single user, single process, no migrations
Base.metadata.create_all(bind=engine)

app.include_router(profile.router)
