"""
ResilienceLab — Remediation Action & Approval Models
Manages planned, approved, and executed remediation actions with safety policies.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from sqlalchemy import (
    Boolean,
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


class RemediationState(str, Enum):
    """Lifecycle states for remediation actions."""
    PROPOSED = "PROPOSED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class RemediationRisk(str, Enum):
    """Risk levels determining approval requirement."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalDecision(str, Enum):
    """Human decision state."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class RemediationAction(IncidentBase):
    """SQLAlchemy model representing a planned or executed remediation operation."""

    __tablename__ = "remediations"

    id = Column(String(50), primary_key=True, index=True)
    incident_id = Column(String(50), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    runbook_id = Column(String(100), nullable=True)
    action_type = Column(String(50), nullable=False)
    target_resource = Column(String(100), nullable=False)
    namespace = Column(String(50), nullable=False, default="resiliencelab")
    parameters = Column(JSON, nullable=True)
    risk_level = Column(String(20), nullable=False, default=RemediationRisk.LOW.value)
    requires_approval = Column(Boolean, default=False, nullable=False)
    state = Column(String(30), nullable=False, default=RemediationState.PROPOSED.value, index=True)
    dry_run = Column(Boolean, default=True, nullable=False)
    execution_output = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    executed_at = Column(DateTime(timezone=True), nullable=True)

    incident = relationship("Incident", back_populates="remediations")
    approvals = relationship("ApprovalRequest", back_populates="remediation", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "runbook_id": self.runbook_id,
            "action_type": self.action_type,
            "target_resource": self.target_resource,
            "namespace": self.namespace,
            "parameters": self.parameters or {},
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "state": self.state,
            "dry_run": self.dry_run,
            "execution_output": self.execution_output,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "approvals": [a.to_dict() for a in self.approvals] if self.approvals else [],
        }


class ApprovalRequest(IncidentBase):
    """SQLAlchemy model representing a human approval requirement."""

    __tablename__ = "approvals"

    id = Column(String(50), primary_key=True, index=True)
    incident_id = Column(String(50), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    remediation_id = Column(String(50), ForeignKey("remediations.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(100), nullable=False)
    reason = Column(Text, nullable=False)
    risk_level = Column(String(20), nullable=False)
    approver = Column(String(100), nullable=True)
    decision = Column(String(20), nullable=False, default=ApprovalDecision.PENDING.value, index=True)
    decision_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    remediation = relationship("RemediationAction", back_populates="approvals")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "remediation_id": self.remediation_id,
            "action": self.action,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "approver": self.approver,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
