"""
ResilienceLab — Incident & Alert ORM Models
Defines the Incident and IncidentAlert schemas with state machine enums.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import relationship

from automation.incident.models.database import IncidentBase


class IncidentState(str, Enum):
    """Formal lifecycle states for an incident."""
    TRIGGERED = "TRIGGERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVESTIGATING = "INVESTIGATING"
    REMEDIATING = "REMEDIATING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class IncidentSeverity(str, Enum):
    """Severity classification."""
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class ConfidenceLevel(str, Enum):
    """Diagnostic confidence level."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Incident(IncidentBase):
    """SQLAlchemy model representing an aggregated incident."""

    __tablename__ = "incidents"

    id = Column(String(50), primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), nullable=False, default=IncidentSeverity.WARNING.value)
    state = Column(String(30), nullable=False, default=IncidentState.TRIGGERED.value, index=True)
    affected_service = Column(String(100), nullable=False, index=True)
    namespace = Column(String(50), nullable=False, default="resiliencelab")
    fingerprint = Column(String(100), nullable=False, index=True)
    source_alert = Column(String(100), nullable=False)
    suspected_cause = Column(Text, nullable=True)
    confidence = Column(String(20), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    extra_data = Column(JSON, nullable=True)

    # Relationships
    alerts = relationship("IncidentAlert", back_populates="incident", cascade="all, delete-orphan")
    timeline = relationship("IncidentTimeline", back_populates="incident", cascade="all, delete-orphan", order_by="IncidentTimeline.timestamp")
    remediations = relationship("RemediationAction", back_populates="incident", cascade="all, delete-orphan", order_by="RemediationAction.created_at")

    def to_dict(self, include_relations: bool = False) -> Dict[str, Any]:
        """Convert model to dictionary representation."""
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "state": self.state,
            "affected_service": self.affected_service,
            "namespace": self.namespace,
            "fingerprint": self.fingerprint,
            "source_alert": self.source_alert,
            "suspected_cause": self.suspected_cause,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "extra_data": self.extra_data or {},
            "alert_count": len(self.alerts) if self.alerts else 0,
        }
        if include_relations:
            data["alerts"] = [a.to_dict() for a in self.alerts] if self.alerts else []
            data["timeline"] = [t.to_dict() for t in self.timeline] if self.timeline else []
            data["remediations"] = [r.to_dict() for r in self.remediations] if self.remediations else []
        return data


class IncidentAlert(IncidentBase):
    """SQLAlchemy model representing an ingested Alertmanager alert linked to an incident."""

    __tablename__ = "incident_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String(50), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    fingerprint = Column(String(100), nullable=False, index=True)
    alertname = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)  # "firing" or "resolved"
    severity = Column(String(20), nullable=False)
    service = Column(String(100), nullable=True)
    pod = Column(String(100), nullable=True)
    namespace = Column(String(50), nullable=True)
    summary = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    labels = Column(JSON, nullable=True)
    annotations = Column(JSON, nullable=True)
    starts_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    incident = relationship("Incident", back_populates="alerts")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "fingerprint": self.fingerprint,
            "alertname": self.alertname,
            "status": self.status,
            "severity": self.severity,
            "service": self.service,
            "pod": self.pod,
            "namespace": self.namespace,
            "summary": self.summary,
            "description": self.description,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
        }
