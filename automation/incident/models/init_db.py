"""
ResilienceLab — Incident Database Initializer & Migration Helper
Creates and verifies tables in PostgreSQL for the Incident Response Engine.
"""

import logging
import sys
from sqlalchemy import inspect, text

from automation.incident.models.database import engine, IncidentBase
import automation.incident.models  # Ensure all models are registered with IncidentBase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("incident.init_db")


def init_incident_tables() -> bool:
    """Create all incident management tables if they do not exist."""
    try:
        logger.info("Connecting to PostgreSQL at: %s", engine.url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection verified.")

        logger.info("Creating incident management tables (if not exist)...")
        IncidentBase.metadata.create_all(bind=engine)

        # Inspect and print created tables
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        logger.info("Available tables in database: %s", tables)

        required_tables = {"incidents", "incident_alerts", "incident_timeline", "remediations", "approvals"}
        missing = required_tables - set(tables)
        if missing:
            logger.error("Missing required incident tables: %s", missing)
            return False

        logger.info("All incident management tables verified successfully.")
        return True
    except Exception as exc:
        logger.error("Failed to initialize incident tables: %s", exc)
        return False


if __name__ == "__main__":
    success = init_incident_tables()
    sys.exit(0 if success else 1)
