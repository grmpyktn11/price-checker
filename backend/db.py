import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

SQLITE_PREFIX = "sqlite:///"
# the repo root, from this file rather than the cwd
ROOT = Path(__file__).resolve().parents[1]


# "sqlite:///./app.db" is relative to the working directory, so launching uvicorn from anywhere
# but the repo root silently created a second, empty database - which looks exactly like every
# watched item, conversation and project having vanished. anchor it to the repo instead
def resolve_url(url: str) -> str:
    if not url.startswith(SQLITE_PREFIX):
        return url
    path = url[len(SQLITE_PREFIX):]
    # ":memory:" and an already-absolute path are left alone
    if not path or path.startswith(":") or Path(path).is_absolute():
        return url
    return f"{SQLITE_PREFIX}{(ROOT / path).resolve()}"


DATABASE_URL = resolve_url(os.getenv("DATABASE_URL", "sqlite:///./app.db"))

# FastAPI serves requests from a threadpool; SQLite blocks cross-thread connections without this
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# request-scoped session, closed when the request ends
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
