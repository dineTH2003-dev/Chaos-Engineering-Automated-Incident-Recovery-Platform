"""
ResilienceLab — Incident Timeline Model
Tracks sequential audit events throughout the incident lifecycle.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship

from automation.incident.models.database import IncidentBase


class IncidentTimeline(IncidentBase):
    """SQLAlchemy model representing an event on the incident timeline."""

    __tablename__ = "incident_timeline"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String(50), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    event_type = Column(String(50), nullable=False)
    summary = Column(String(255), nullable=False)
    details = Column(JSON, nullable=True)
    actor = Column(String(100), nullable=False, default="SYSTEM")

    incident = relationship("Incident", back_populates="timeline")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "event_type": self.event_type,
            "summary": self.summary,
            "details": self.details or {},
            "actor": self.actor,
        }
