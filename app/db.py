import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe",
)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Lightweight, owner-scoped invalidation for the SSE workspace cache. Never
# affects transaction success or writes clinical content to Redis.
from .services.workspace_revision_signals import install_revision_events

install_revision_events()
