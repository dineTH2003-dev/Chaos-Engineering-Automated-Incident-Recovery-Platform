"""
ResilienceLab — Incident Database Engine & Session Provider
Manages connection pooling and sessions for incident state persistence.
"""

from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from automation.incident.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

IncidentBase = declarative_base()


def get_incident_db() -> Generator[Session, None, None]:
    """Provide a transactional database session for FastAPI dependencies."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Alias for dependency injection
get_db = get_incident_db


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager for standalone repository operations."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
