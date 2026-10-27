"""
ResilienceLab — Incident Timeline Repository
Data access layer for IncidentTimeline audit events.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from automation.incident.models.timeline import IncidentTimeline

logger = logging.getLogger("incident.timeline_repo")


class TimelineRepository:
    """Repository for managing incident timeline events."""

    def __init__(self, db: Session):
        self.db = db

    def record_event(
        self,
        incident_id: str,
        event_type: str,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
        actor: str = "SYSTEM",
    ) -> IncidentTimeline:
        """Create and persist an audit event on an incident's timeline."""
        event = IncidentTimeline(
            incident_id=incident_id,
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            summary=summary,
            details=details or {},
            actor=actor,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        logger.debug(
            "Recorded timeline event for %s [%s]: %s (actor=%s)",
            incident_id,
            event_type,
            summary,
            actor,
        )
        return event

    def list_events(self, incident_id: str) -> List[IncidentTimeline]:
        """Return all timeline events for an incident ordered chronologically."""
        return (
            self.db.query(IncidentTimeline)
            .filter(IncidentTimeline.incident_id == incident_id)
            .order_by(IncidentTimeline.timestamp.asc())
            .all()
        )
