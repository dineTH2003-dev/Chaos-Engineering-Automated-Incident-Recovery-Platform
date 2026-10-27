"""
ResilienceLab — Incident Response Platform Main Application
Central ASGI entrypoint combining alert ingestion, incident management, automated diagnosis,
safe remediation, recovery verification, and postmortem generation.
"""

from contextlib import asynccontextmanager
import logging
from typing import Any, Dict
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from automation.incident.api.routes import alerts, incidents, slo
from automation.incident.config import settings
from automation.incident.models.database import IncidentBase, engine

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [incident-engine] %(message)s",
)
logger = logging.getLogger("incident.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize database tables and background workers."""
    logger.info("Initializing ResilienceLab Incident Response Engine v%s...", settings.VERSION)
    try:
        IncidentBase.metadata.create_all(bind=engine)
        logger.info("Database schemas verified successfully.")
    except Exception as exc:
        logger.warning("Could not auto-create tables on startup (will retry on demand): %s", exc)
    yield
    logger.info("Shutting down Incident Response Engine.")


app = FastAPI(
    title="ResilienceLab Incident Response & Automated Remediation Platform",
    description=(
        "Next-generation SRE platform ingesting Alertmanager alerts, deduplicating incident streams, "
        "correlating multi-service failures, executing policy-guarded remediations, and verifying recovery."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(alerts.router)
app.include_router(incidents.router)
app.include_router(slo.router)


@app.get("/health", tags=["system"], summary="Liveness & Readiness probe")
def health() -> Dict[str, Any]:
    """Health probe endpoint compatible with Kubernetes probes."""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.VERSION,
        "dry_run": settings.DRY_RUN,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("automation.incident.api.main:app", host="0.0.0.0", port=5001, reload=False)
