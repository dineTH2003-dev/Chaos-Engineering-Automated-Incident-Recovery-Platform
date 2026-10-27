"""
ResilienceLab — Incident Repository
Data access layer for Incident and IncidentAlert entities.
"""

from datetime import datetime, timezone
import logging
from typing import List, Optional
from sqlalchemy.orm import Session, joinedload

from automation.incident.models.incident import Incident, IncidentAlert, IncidentState

logger = logging.getLogger("incident.repo")


class IncidentRepository:
    """Repository for managing incidents and attached alerts in PostgreSQL."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, incident: Incident) -> Incident:
        """Persist a new incident."""
        self.db.add(incident)
        self.db.commit()
        self.db.refresh(incident)
        logger.info("Created incident %s (state=%s, service=%s)", incident.id, incident.state, incident.affected_service)
        return incident

    def get_by_id(self, incident_id: str) -> Optional[Incident]:
        """Retrieve an incident by ID with alerts, timeline, and remediations eagerly loaded."""
        return (
            self.db.query(Incident)
            .options(
                joinedload(Incident.alerts),
                joinedload(Incident.timeline),
                joinedload(Incident.remediations),
            )
            .filter(Incident.id == incident_id)
            .first()
        )

    def get_active_by_fingerprint(self, fingerprint: str) -> Optional[Incident]:
        """Find an active (non-resolved, non-failed) incident by its alert fingerprint."""
        terminal_states = {IncidentState.RESOLVED.value, IncidentState.FAILED.value}
        return (
            self.db.query(Incident)
            .filter(
                Incident.fingerprint == fingerprint,
                Incident.state.notin_(terminal_states),
            )
            .first()
        )

    def get_active_by_service(self, service: str, namespace: str = "resiliencelab") -> List[Incident]:
        """Find active incidents targeting a service in a namespace."""
        terminal_states = {IncidentState.RESOLVED.value, IncidentState.FAILED.value}
        return (
            self.db.query(Incident)
            .filter(
                Incident.affected_service == service,
                Incident.namespace == namespace,
                Incident.state.notin_(terminal_states),
            )
            .all()
        )

    def list_incidents(
        self,
        state: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> List[Incident]:
        """List incidents with optional state and severity filters."""
        query = self.db.query(Incident)
        if state:
            query = query.filter(Incident.state == state)
        if severity:
            query = query.filter(Incident.severity == severity)
        return query.order_by(Incident.created_at.desc()).limit(limit).all()

    def update_state(self, incident_id: str, new_state: IncidentState) -> Optional[Incident]:
        """Update incident state and refresh updated_at timestamp."""
        incident = self.db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            return None

        incident.state = new_state.value
        incident.updated_at = datetime.now(timezone.utc)
        if new_state == IncidentState.RESOLVED and not incident.resolved_at:
            incident.resolved_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(incident)
        logger.info("Updated incident %s state to %s", incident_id, new_state.value)
        return incident

    def update_diagnosis(
        self,
        incident_id: str,
        suspected_cause: str,
        confidence: str,
        extra_data: Optional[dict] = None,
    ) -> Optional[Incident]:
        """Attach diagnosis findings to the incident."""
        incident = self.db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            return None

        incident.suspected_cause = suspected_cause
        incident.confidence = confidence
        incident.updated_at = datetime.now(timezone.utc)
        if extra_data:
            current = incident.extra_data or {}
            current.update(extra_data)
            incident.extra_data = current

        self.db.commit()
        self.db.refresh(incident)
        return incident

    def add_alert(self, alert: IncidentAlert) -> IncidentAlert:
        """Attach an alert notification to an incident."""
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        return alert
