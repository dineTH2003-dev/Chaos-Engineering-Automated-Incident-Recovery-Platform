"""
ResilienceLab — Incident Lifecycle Service
State machine enforcing strict lifecycle transitions and audit timeline recording.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional, Set
from sqlalchemy.orm import Session

from automation.incident.models.incident import Incident, IncidentState
from automation.incident.repositories.incident_repo import IncidentRepository
from automation.incident.repositories.timeline_repo import TimelineRepository

logger = logging.getLogger("incident.lifecycle")


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal lifecycle state transition is requested."""
    pass


class IncidentLifecycleService:
    """Enforces state machine transitions for incidents and logs audit trail."""

    # Explicit allowed transitions
    VALID_TRANSITIONS: Dict[IncidentState, Set[IncidentState]] = {
        IncidentState.TRIGGERED: {
            IncidentState.ACKNOWLEDGED,
            IncidentState.INVESTIGATING,
            IncidentState.ESCALATED,
        },
        IncidentState.ACKNOWLEDGED: {
            IncidentState.INVESTIGATING,
            IncidentState.REMEDIATING,
            IncidentState.ESCALATED,
        },
        IncidentState.INVESTIGATING: {
            IncidentState.REMEDIATING,
            IncidentState.VERIFYING,
            IncidentState.RESOLVED,
            IncidentState.ESCALATED,
            IncidentState.FAILED,
        },
        IncidentState.REMEDIATING: {
            IncidentState.VERIFYING,
            IncidentState.FAILED,
            IncidentState.ESCALATED,
        },
        IncidentState.VERIFYING: {
            IncidentState.RESOLVED,
            IncidentState.FAILED,
            IncidentState.REMEDIATING,
            IncidentState.ESCALATED,
        },
        IncidentState.FAILED: {
            IncidentState.INVESTIGATING,
            IncidentState.ESCALATED,
            IncidentState.RESOLVED,
        },
        IncidentState.ESCALATED: {
            IncidentState.INVESTIGATING,
            IncidentState.REMEDIATING,
            IncidentState.RESOLVED,
        },
        IncidentState.RESOLVED: {
            IncidentState.TRIGGERED,  # Allow reopening on regression
        },
    }

    def __init__(self, db: Session):
        self.db = db
        self.incident_repo = IncidentRepository(db)
        self.timeline_repo = TimelineRepository(db)

    def transition(
        self,
        incident_id: str,
        target_state: IncidentState,
        actor: str = "SYSTEM",
        reason: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Incident:
        """
        Transition an incident to target_state with validation and audit timeline logging.
        """
        incident = self.incident_repo.get_by_id(incident_id)
        if not incident:
            raise ValueError(f"Incident with ID {incident_id} not found")

        current_state = IncidentState(incident.state)

        # Idempotent no-op
        if current_state == target_state:
            logger.debug("Incident %s is already in state %s; no-op", incident_id, target_state.value)
            return incident

        allowed_targets = self.VALID_TRANSITIONS.get(current_state, set())
        if target_state not in allowed_targets:
            msg = (
                f"Invalid transition for incident {incident_id}: "
                f"{current_state.value} -> {target_state.value}. "
                f"Allowed transitions from {current_state.value} are: "
                f"{[s.value for s in allowed_targets]}"
            )
            logger.error(msg)
            raise InvalidStateTransitionError(msg)

        # Apply state update
        updated_incident = self.incident_repo.update_state(incident_id, target_state)

        # Record timeline event
        summary = f"State transitioned from {current_state.value} to {target_state.value}"
        if reason:
            summary += f": {reason}"

        timeline_details = {
            "from_state": current_state.value,
            "to_state": target_state.value,
            "reason": reason,
            **(details or {}),
        }

        self.timeline_repo.record_event(
            incident_id=incident_id,
            event_type=f"STATE_TRANSITION_{target_state.value}",
            summary=summary,
            details=timeline_details,
            actor=actor,
        )

        logger.info(
            "Incident %s transitioned: %s -> %s by %s",
            incident_id,
            current_state.value,
            target_state.value,
            actor,
        )
        return updated_incident

    # Convenience lifecycle actions
    def acknowledge(self, incident_id: str, actor: str = "SYSTEM", reason: Optional[str] = None) -> Incident:
        return self.transition(incident_id, IncidentState.ACKNOWLEDGED, actor=actor, reason=reason)

    def start_investigation(self, incident_id: str, actor: str = "SYSTEM", reason: Optional[str] = None) -> Incident:
        return self.transition(incident_id, IncidentState.INVESTIGATING, actor=actor, reason=reason)

    def start_remediation(self, incident_id: str, actor: str = "SYSTEM", reason: Optional[str] = None) -> Incident:
        return self.transition(incident_id, IncidentState.REMEDIATING, actor=actor, reason=reason)

    def start_verification(self, incident_id: str, actor: str = "SYSTEM", reason: Optional[str] = None) -> Incident:
        return self.transition(incident_id, IncidentState.VERIFYING, actor=actor, reason=reason)

    def resolve(self, incident_id: str, actor: str = "SYSTEM", reason: Optional[str] = None) -> Incident:
        return self.transition(incident_id, IncidentState.RESOLVED, actor=actor, reason=reason)

    def fail(self, incident_id: str, actor: str = "SYSTEM", reason: Optional[str] = None) -> Incident:
        return self.transition(incident_id, IncidentState.FAILED, actor=actor, reason=reason)

    def escalate(self, incident_id: str, actor: str = "SYSTEM", reason: Optional[str] = None) -> Incident:
        return self.transition(incident_id, IncidentState.ESCALATED, actor=actor, reason=reason)
