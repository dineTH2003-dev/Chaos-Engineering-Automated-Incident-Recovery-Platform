"""
ResilienceLab — Alert Ingestion API Route
Maintains 100% backward compatibility with Alertmanager webhook receiver while
storing structured incidents in PostgreSQL.
"""

import logging
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from automation.incident.models.database import get_db
from automation.incident.services.ingestion_service import (
    AlertIngestionService,
    clear_raw_alerts_buffer,
    get_raw_alerts_buffer,
)

logger = logging.getLogger("incident.api.alerts")
router = APIRouter(tags=["alerts"])


@router.post("/alerts", summary="Ingest Alertmanager alerts")
async def receive_alerts(
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Ingest, normalize, deduplicate, and record Alertmanager alerts into PostgreSQL.
    Compatible with Prometheus Alertmanager webhook specification.
    """
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception as exc:
        logger.error("Failed to parse incoming alert JSON: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    service = AlertIngestionService(db)
    affected_incidents, count = service.process_webhook_payload(payload)

    return {
        "status": "processed",
        "alerts_processed": count,
        "affected_incidents": affected_incidents,
        "total_recorded": len(get_raw_alerts_buffer()),
    }


@router.get("/alerts", summary="List raw alert buffer (backward compatible)")
def list_alerts() -> List[Dict[str, Any]]:
    """Return in-memory buffer of raw alert events for compatibility."""
    return get_raw_alerts_buffer()


@router.delete("/alerts", summary="Clear raw alert buffer (backward compatible)")
def clear_alerts() -> Dict[str, str]:
    """Clear in-memory buffer of raw alert events."""
    clear_raw_alerts_buffer()
    return {"status": "cleared"}
