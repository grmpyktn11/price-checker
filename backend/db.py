import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

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
