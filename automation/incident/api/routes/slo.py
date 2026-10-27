"""
ResilienceLab — SRE SLO API Route
Exposes real-time availability SLI, latency SLI, error budget burn rates,
and incident impact consumption.
"""

from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from automation.incident.models.database import get_db
from automation.incident.repositories.incident_repo import IncidentRepository
from automation.incident.slo.engine import SLOEngine

router = APIRouter(prefix="/api/slo", tags=["slo"])


@router.get("", summary="Get global SRE SLO & Error Budget health")
def get_slo_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve current rolling availability SLI, latency SLI, and error budget burn rates."""
    repo = IncidentRepository(db)
    incidents = repo.list_incidents(limit=200)

    engine = SLOEngine()
    summary = engine.get_slo_summary(incidents=incidents)

    return {
        "availability_target_pct": summary.availability_target_pct,
        "current_availability_sli_pct": summary.current_availability_sli_pct,
        "latency_target_ms": summary.latency_target_ms,
        "current_p95_latency_ms": summary.current_p95_latency_ms,
        "error_budget_remaining_pct": summary.error_budget_remaining_pct,
        "current_burn_rate": summary.current_burn_rate,
        "fast_burn_active": summary.fast_burn_active,
        "slow_burn_active": summary.slow_burn_active,
        "allowed_downtime_minutes": summary.allowed_downtime_minutes,
        "consumed_downtime_seconds": summary.consumed_downtime_seconds,
        "window": summary.window,
        "total_incidents_recorded": len(incidents),
    }


@router.get("/incidents/{incident_id}", summary="Get error budget impact for a specific incident")
def get_incident_slo_impact(
    incident_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Compute exact error budget percentage consumed by a single incident."""
    repo = IncidentRepository(db)
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )

    engine = SLOEngine()
    return engine.compute_incident_budget_consumption(incident)
