"""
ResilienceLab — Remediation & Approval Repository
Data access layer for RemediationAction and ApprovalRequest entities.
"""

from datetime import datetime, timezone
import logging
from typing import List, Optional
from sqlalchemy.orm import Session

from automation.incident.models.remediation import (
    ApprovalDecision,
    ApprovalRequest,
    RemediationAction,
    RemediationState,
)

logger = logging.getLogger("incident.remediation_repo")


class RemediationRepository:
    """Repository for managing remediation actions and approval workflows."""

    def __init__(self, db: Session):
        self.db = db

    def create_remediation(self, remediation: RemediationAction) -> RemediationAction:
        """Persist a new remediation action."""
        self.db.add(remediation)
        self.db.commit()
        self.db.refresh(remediation)
        logger.info(
            "Created remediation %s for incident %s (action=%s, risk=%s, state=%s)",
            remediation.id,
            remediation.incident_id,
            remediation.action_type,
            remediation.risk_level,
            remediation.state,
        )
        return remediation

    def get_remediation(self, remediation_id: str) -> Optional[RemediationAction]:
        """Retrieve a remediation by ID."""
        return self.db.query(RemediationAction).filter(RemediationAction.id == remediation_id).first()

    def get_by_incident(self, incident_id: str) -> List[RemediationAction]:
        """List all remediations for an incident ordered by created_at."""
        return (
            self.db.query(RemediationAction)
            .filter(RemediationAction.incident_id == incident_id)
            .order_by(RemediationAction.created_at.asc())
            .all()
        )

    def update_remediation_state(
        self,
        remediation_id: str,
        state: RemediationState,
        execution_output: Optional[str] = None,
    ) -> Optional[RemediationAction]:
        """Update remediation state and execution details."""
        remediation = self.get_remediation(remediation_id)
        if not remediation:
            return None

        remediation.state = state.value
        if execution_output is not None:
            remediation.execution_output = execution_output
        if state in {RemediationState.EXECUTING, RemediationState.COMPLETED, RemediationState.FAILED}:
            if not remediation.executed_at:
                remediation.executed_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(remediation)
        logger.info("Updated remediation %s state to %s", remediation_id, state.value)
        return remediation

    def create_approval(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist a new approval request."""
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)
        logger.info(
            "Created approval request %s for incident %s (action=%s, expires_at=%s)",
            approval.id,
            approval.incident_id,
            approval.action,
            approval.expires_at,
        )
        return approval

    def get_approval(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Retrieve an approval request by ID."""
        return self.db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()

    def get_pending_approval_for_incident(self, incident_id: str) -> Optional[ApprovalRequest]:
        """Find pending, unexpired approval for an incident."""
        now = datetime.now(timezone.utc)
        return (
            self.db.query(ApprovalRequest)
            .filter(
                ApprovalRequest.incident_id == incident_id,
                ApprovalRequest.decision == ApprovalDecision.PENDING.value,
                ApprovalRequest.expires_at > now,
            )
            .first()
        )

    def list_approvals_for_incident(self, incident_id: str) -> List[ApprovalRequest]:
        """List all approvals for an incident."""
        return (
            self.db.query(ApprovalRequest)
            .filter(ApprovalRequest.incident_id == incident_id)
            .order_by(ApprovalRequest.created_at.asc())
            .all()
        )

    def decide_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        approver: str,
        reason: Optional[str] = None,
    ) -> Optional[ApprovalRequest]:
        """Record human decision on an approval request."""
        approval = self.get_approval(approval_id)
        if not approval:
            return None

        approval.decision = decision.value
        approval.approver = approver
        approval.decision_reason = reason
        approval.decided_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(approval)
        logger.info(
            "Approval %s decided: %s by %s (reason: %s)",
            approval_id,
            decision.value,
            approver,
            reason,
        )
        return approval

    def expire_stale_approvals(self) -> int:
        """Mark all expired pending approvals as EXPIRED."""
        now = datetime.now(timezone.utc)
        stale = (
            self.db.query(ApprovalRequest)
            .filter(
                ApprovalRequest.decision == ApprovalDecision.PENDING.value,
                ApprovalRequest.expires_at <= now,
            )
            .all()
        )
        count = 0
        for app in stale:
            app.decision = ApprovalDecision.EXPIRED.value
            app.decided_at = now
            app.decision_reason = "Approval request expired before review"
            count += 1
        if count > 0:
            self.db.commit()
            logger.info("Expired %d stale approval requests", count)
        return count
