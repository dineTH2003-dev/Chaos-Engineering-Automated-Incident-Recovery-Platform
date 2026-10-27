"""Database engine, connection pooling, and lifecycle management."""

import logging
import time
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings
from app.metrics import (
    database_connection_errors_total,
    database_connections_active,
)

logger = logging.getLogger(settings.APP_NAME)

# SQLAlchemy synchronous engine with production-ready connection pool settings
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator:
    """Provide a transactional database session dependency for FastAPI.

    Increments the active connections gauge when checked out, and ensures
    the session is closed and gauge decremented upon request completion.

    Yields:
        Session: Scoped database session.
    """
    db = SessionLocal()
    database_connections_active.inc()
    try:
        yield db
    finally:
        database_connections_active.dec()
        db.close()


def check_db_connection() -> bool:
    """Execute a simple query to verify database connectivity.

    Returns:
        bool: True if database responds to query, False otherwise.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("Database connectivity check failed: %s", exc)
        database_connection_errors_total.inc()
        return False


def startup_db(max_attempts: int = 3, initial_backoff: float = 1.0) -> bool:
    """Test database connectivity on startup with bounded retries and exponential backoff.

    Args:
        max_attempts: Total connection attempts before degrading (default: 3).
        initial_backoff: Initial backoff duration in seconds (default: 1.0).

    Returns:
        bool: True if connection is established and schema verified, False if all attempts fail.
    """
    backoff = initial_backoff
    for attempt in range(1, max_attempts + 1):
        logger.info(
            "Testing database connectivity (attempt %d/%d)...",
            attempt,
            max_attempts,
        )
        if check_db_connection():
            logger.info("Database connectivity established successfully.")
            try:
                # Ensure all models are registered before creating tables
                import app.models  # noqa: F401

                Base.metadata.create_all(bind=engine)
                logger.info("Database tables verified and initialized.")
            except Exception as schema_exc:
                logger.error("Failed to initialize database tables: %s", schema_exc)
            return True

        if attempt < max_attempts:
            logger.warning(
                "Database connection attempt %d failed. Retrying in %.1fs...",
                attempt,
                backoff,
            )
            time.sleep(backoff)
            backoff *= 2

    logger.error(
        "Failed to establish database connection after %d attempts. "
        "Proceeding in degraded mode (readiness probe will report disconnected).",
        max_attempts,
    )
    return False


def shutdown_db() -> None:
    """Dispose of the database engine and close all idle connections in the pool."""
    logger.info("Disposing database connection pool...")
    engine.dispose()
    logger.info("Database connection pool disposed successfully.")
