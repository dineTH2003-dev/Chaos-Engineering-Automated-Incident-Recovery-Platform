"""
ResilienceLab — Incidents API Route
Provides endpoints for incident querying, lifecycle state transitions, and audit inspection.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from automation.incident.config import settings
from automation.incident.models.database import get_db
from automation.incident.models.incident import IncidentState
from automation.incident.models.remediation import ApprovalDecision
from automation.incident.repositories.incident_repo import IncidentRepository
from automation.incident.repositories.timeline_repo import TimelineRepository
from automation.incident.services.lifecycle_service import (
    IncidentLifecycleService,
    InvalidStateTransitionError,
)

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


class AcknowledgeRequest(BaseModel):
    actor: str = "SRE_ON_CALL"
    reason: Optional[str] = "Acknowledged via Incident API"


class TransitionRequest(BaseModel):
    target_state: IncidentState
    actor: str = "SRE_OPERATOR"
    reason: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@router.get("", summary="List incidents")
def list_incidents(
    state: Optional[str] = Query(None, description="Filter by state (TRIGGERED, INVESTIGATING, etc.)"),
    severity: Optional[str] = Query(None, description="Filter by severity (critical, warning, info)"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List incidents with optional state/severity filters."""
    repo = IncidentRepository(db)
    incidents = repo.list_incidents(state=state, severity=severity, limit=limit)
    return [inc.to_dict() for inc in incidents]


@router.get("/{incident_id}", summary="Get incident details")
def get_incident(
    incident_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve full incident record with timeline, alerts, and remediations."""
    repo = IncidentRepository(db)
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )
    return incident.to_dict(include_relations=True)


@router.post("/{incident_id}/acknowledge", summary="Acknowledge an incident")
def acknowledge_incident(
    incident_id: str,
    req: AcknowledgeRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Acknowledge an incident and record the event on the timeline."""
    lifecycle = IncidentLifecycleService(db)
    try:
        updated = lifecycle.acknowledge(incident_id, actor=req.actor, reason=req.reason)
        return updated.to_dict()
    except InvalidStateTransitionError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))


@router.post("/{incident_id}/transition", summary="Transition incident state")
def transition_incident(
    incident_id: str,
    req: TransitionRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Transition an incident through its lifecycle state machine."""
    lifecycle = IncidentLifecycleService(db)
    try:
        updated = lifecycle.transition(
            incident_id=incident_id,
            target_state=req.target_state,
            actor=req.actor,
            reason=req.reason,
            details=req.details,
        )
        return updated.to_dict()
    except InvalidStateTransitionError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))


@router.get("/{incident_id}/timeline", summary="Get incident audit timeline")
def get_incident_timeline(
    incident_id: str,
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieve chronological audit timeline events for an incident."""
    repo = IncidentRepository(db)
    if not repo.get_by_id(incident_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )
    timeline_repo = TimelineRepository(db)
    events = timeline_repo.list_events(incident_id)
    return [event.to_dict() for event in events]


@router.post("/{incident_id}/diagnose", summary="Trigger automated multi-source diagnosis")
def diagnose_incident(
    incident_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Execute automated multi-source diagnosis and update incident analysis."""
    from automation.incident.diagnosis.engine import AutomatedDiagnosisEngine

    repo = IncidentRepository(db)
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )

    diagnosis_engine = AutomatedDiagnosisEngine()
    report = diagnosis_engine.diagnose(incident)

    # Update incident in DB with diagnosis results
    repo.update_diagnosis(
        incident_id=incident_id,
        suspected_cause=report.suspected_cause,
        confidence=report.confidence.value,
        extra_data={
            "evidence": report.evidence,
            "recommended_runbook": report.recommended_runbook,
            "recommended_actions": report.recommended_actions,
        },
    )

    # Advance state to INVESTIGATING if currently in TRIGGERED / ACKNOWLEDGED
    lifecycle = IncidentLifecycleService(db)
    if incident.state in {IncidentState.TRIGGERED.value, IncidentState.ACKNOWLEDGED.value}:
        lifecycle.start_investigation(
            incident_id=incident_id,
            actor="AUTO_DIAGNOSIS",
            reason=f"Automated diagnosis completed: {report.suspected_cause[:100]}",
        )

    # Record timeline event
    timeline_repo = TimelineRepository(db)
    timeline_repo.record_event(
        incident_id=incident_id,
        event_type="DIAGNOSIS_COMPLETED",
        summary=f"Automated diagnosis ({report.confidence.value} confidence): {report.suspected_cause}",
        details={
            "suspected_cause": report.suspected_cause,
            "confidence": report.confidence.value,
            "recommended_runbook": report.recommended_runbook,
            "recommended_actions": report.recommended_actions,
        },
        actor="AUTO_DIAGNOSIS",
    )

    return {
        "incident_id": incident_id,
        "suspected_cause": report.suspected_cause,
        "confidence": report.confidence.value,
        "recommended_runbook": report.recommended_runbook,
        "recommended_actions": report.recommended_actions,
        "evidence": report.evidence,
    }


class RemediateRequest(BaseModel):
    explicit_runbook_id: Optional[str] = None
    dry_run: Optional[bool] = None
    actor: str = "SRE_AUTOMATION"


class ApproveRemediationRequest(BaseModel):
    approval_id: str
    decision: ApprovalDecision
    approver: str = "SRE_ON_CALL"
    reason: Optional[str] = None
    dry_run: Optional[bool] = None


@router.post("/{incident_id}/remediate", summary="Trigger or stage automated remediation")
def remediate_incident(
    incident_id: str,
    req: RemediateRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Select appropriate runbook, validate safety policies, and either execute
    auto-approved low-risk actions or create human approval requests.
    """
    from datetime import datetime, timedelta, timezone
    import hashlib
    import os
    from automation.incident.models.remediation import (
        ApprovalRequest,
        RemediationAction,
        RemediationState,
    )
    from automation.incident.remediation.executor import RemediationExecutor
    from automation.incident.repositories.remediation_repo import RemediationRepository
    from automation.incident.runbooks.engine import RunbookSelectionEngine

    repo = IncidentRepository(db)
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )

    # Transition to REMEDIATING if valid
    lifecycle = IncidentLifecycleService(db)
    if incident.state in {
        IncidentState.TRIGGERED.value,
        IncidentState.ACKNOWLEDGED.value,
        IncidentState.INVESTIGATING.value,
    }:
        lifecycle.start_remediation(
            incident_id=incident_id,
            actor=req.actor,
            reason="Starting runbook-guided remediation",
        )

    runbook_engine = RunbookSelectionEngine()
    plan = runbook_engine.select_runbook(incident, explicit_runbook_id=req.explicit_runbook_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No matching runbook found for incident",
        )

    remediation_repo = RemediationRepository(db)
    timeline_repo = TimelineRepository(db)
    executor = RemediationExecutor()

    executed_actions = []
    pending_approvals = []

    for action_plan in plan.actions:
        rem_id = f"REM-{hashlib.sha256(os.urandom(8)).hexdigest()[:6].upper()}"
        rem_action = RemediationAction(
            id=rem_id,
            incident_id=incident_id,
            runbook_id=plan.runbook_id,
            action_type=action_plan.action_type,
            target_resource=action_plan.target_resource,
            namespace=incident.namespace or "resiliencelab",
            parameters=action_plan.parameters,
            risk_level=action_plan.risk_level,
            requires_approval=action_plan.requires_approval,
            state=RemediationState.PROPOSED.value,
            dry_run=(req.dry_run if req.dry_run is not None else settings.DRY_RUN),
        )

        if action_plan.requires_approval:
            # Stage action and request human approval
            rem_action.state = RemediationState.PENDING_APPROVAL.value
            remediation_repo.create_remediation(rem_action)

            app_id = f"APP-{hashlib.sha256(os.urandom(8)).hexdigest()[:6].upper()}"
            approval_req = ApprovalRequest(
                id=app_id,
                incident_id=incident_id,
                remediation_id=rem_id,
                action=f"{action_plan.action_type} on {action_plan.target_resource}",
                reason=action_plan.description,
                risk_level=action_plan.risk_level,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.APPROVAL_TIMEOUT_SECONDS),
            )
            remediation_repo.create_approval(approval_req)

            timeline_repo.record_event(
                incident_id=incident_id,
                event_type="APPROVAL_REQUESTED",
                summary=f"Human approval required: {action_plan.description} (Risk: {action_plan.risk_level})",
                details=approval_req.to_dict(),
                actor="REMEDIATION_ENGINE",
            )
            pending_approvals.append(approval_req.to_dict())
            executed_actions.append(rem_action.to_dict())
        else:
            # Auto-approved or low-risk: execute immediately
            rem_action.state = RemediationState.EXECUTING.value
            remediation_repo.create_remediation(rem_action)

            res = executor.execute(rem_action, override_dry_run=req.dry_run)
            final_state = RemediationState.COMPLETED if res.success else RemediationState.FAILED
            remediation_repo.update_remediation_state(rem_id, final_state, execution_output=res.output)

            event_type = "REMEDIATION_DRY_RUN" if res.dry_run else "REMEDIATION_EXECUTED"
            timeline_repo.record_event(
                incident_id=incident_id,
                event_type=event_type,
                summary=f"Remediation step {action_plan.step}: {res.output[:150]}",
                details={
                    "remediation_id": rem_id,
                    "action_type": action_plan.action_type,
                    "target_resource": action_plan.target_resource,
                    "dry_run": res.dry_run,
                    "success": res.success,
                    "output": res.output,
                },
                actor=req.actor,
            )
            executed_actions.append(rem_action.to_dict())

    return {
        "incident_id": incident_id,
        "runbook_id": plan.runbook_id,
        "runbook_name": plan.name,
        "actions": executed_actions,
        "pending_approvals": pending_approvals,
        "approval_required": len(pending_approvals) > 0,
    }


@router.post("/{incident_id}/approve-remediation", summary="Approve or reject pending remediation")
def approve_remediation(
    incident_id: str,
    req: ApproveRemediationRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Human approval gate for sensitive / high-risk remediation actions."""
    from automation.incident.models.remediation import (
        ApprovalDecision,
        RemediationState,
    )
    from automation.incident.remediation.executor import RemediationExecutor
    from automation.incident.repositories.remediation_repo import RemediationRepository

    repo = IncidentRepository(db)
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )

    remediation_repo = RemediationRepository(db)
    approval = remediation_repo.get_approval(req.approval_id)
    if not approval or approval.incident_id != incident_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request {req.approval_id} not found for this incident",
        )

    if approval.decision != ApprovalDecision.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Approval request {req.approval_id} is already in state '{approval.decision}'",
        )

    decided_approval = remediation_repo.decide_approval(
        approval_id=req.approval_id,
        decision=req.decision,
        approver=req.approver,
        reason=req.reason,
    )

    remediation = remediation_repo.get_remediation(approval.remediation_id)
    timeline_repo = TimelineRepository(db)
    lifecycle = IncidentLifecycleService(db)

    if req.decision == ApprovalDecision.REJECTED:
        remediation_repo.update_remediation_state(
            remediation.id,
            RemediationState.REJECTED,
            execution_output=f"Rejected by {req.approver}: {req.reason}",
        )
        timeline_repo.record_event(
            incident_id=incident_id,
            event_type="APPROVAL_REJECTED",
            summary=f"Remediation rejected by {req.approver}: {req.reason}",
            details={"approval_id": req.approval_id, "approver": req.approver, "reason": req.reason},
            actor=req.approver,
        )
        lifecycle.escalate(incident_id, actor=req.approver, reason="Remediation rejected by operator")
        return {
            "status": "rejected",
            "approval": decided_approval.to_dict(),
            "remediation": remediation.to_dict(),
        }

    # Approved: Execute the remediation
    remediation_repo.update_remediation_state(remediation.id, RemediationState.APPROVED)
    timeline_repo.record_event(
        incident_id=incident_id,
        event_type="APPROVAL_GRANTED",
        summary=f"Remediation approved by {req.approver}: {req.reason}",
        details={"approval_id": req.approval_id, "approver": req.approver},
        actor=req.approver,
    )

    executor = RemediationExecutor()
    res = executor.execute(remediation, approval=decided_approval, override_dry_run=req.dry_run)
    final_state = RemediationState.COMPLETED if res.success else RemediationState.FAILED
    updated_rem = remediation_repo.update_remediation_state(remediation.id, final_state, execution_output=res.output)

    event_type = "REMEDIATION_DRY_RUN" if res.dry_run else "REMEDIATION_EXECUTED"
    timeline_repo.record_event(
        incident_id=incident_id,
        event_type=event_type,
        summary=f"Approved remediation executed: {res.output[:150]}",
        details={
            "remediation_id": remediation.id,
            "dry_run": res.dry_run,
            "success": res.success,
            "output": res.output,
        },
        actor=req.approver,
    )

    # Transition incident state to VERIFYING
    if incident.state == IncidentState.REMEDIATING.value:
        lifecycle.start_verification(
            incident_id=incident_id,
            actor=req.approver,
            reason="Remediation executed; entering recovery verification",
        )

    return {
        "status": "executed",
        "approval": decided_approval.to_dict(),
        "remediation": updated_rem.to_dict() if updated_rem else None,
        "execution_result": {
            "success": res.success,
            "dry_run": res.dry_run,
            "output": res.output,
            "error": res.error,
        },
    }


@router.post("/{incident_id}/verify", summary="Verify recovery and resolve incident")
def verify_incident_recovery(
    incident_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Independently verify multi-step recovery.
    Transitions incident to RESOLVED only if all verification checks pass.
    """
    from automation.incident.verification.engine import RecoveryVerificationEngine

    repo = IncidentRepository(db)
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )

    lifecycle = IncidentLifecycleService(db)
    # Ensure in VERIFYING state if not already
    if incident.state in {
        IncidentState.INVESTIGATING.value,
        IncidentState.REMEDIATING.value,
    }:
        lifecycle.start_verification(
            incident_id=incident_id,
            actor="VERIFICATION_ENGINE",
            reason="Starting automated recovery verification",
        )

    verifier = RecoveryVerificationEngine()
    report = verifier.verify_recovery(incident)

    timeline_repo = TimelineRepository(db)

    if report.is_healthy:
        lifecycle.resolve(
            incident_id=incident_id,
            actor="VERIFICATION_ENGINE",
            reason=f"Recovery verified: {report.summary}",
        )
        timeline_repo.record_event(
            incident_id=incident_id,
            event_type="RECOVERY_VERIFIED",
            summary=f"Incident resolved: {report.summary}",
            details={
                "consecutive_successes": report.consecutive_successes,
                "required_successes": report.required_successes,
                "deployment_status": report.deployment_status,
                "telemetry_status": report.telemetry_status,
            },
            actor="VERIFICATION_ENGINE",
        )
    else:
        timeline_repo.record_event(
            incident_id=incident_id,
            event_type="VERIFICATION_FAILED",
            summary=f"Recovery verification failed: {report.summary}",
            details={
                "consecutive_successes": report.consecutive_successes,
                "deployment_status": report.deployment_status,
            },
            actor="VERIFICATION_ENGINE",
        )

    return {
        "incident_id": incident_id,
        "is_healthy": report.is_healthy,
        "status": report.status,
        "summary": report.summary,
        "consecutive_successes": report.consecutive_successes,
        "required_successes": report.required_successes,
        "deployment_status": report.deployment_status,
        "telemetry_status": report.telemetry_status,
    }


@router.get("/{incident_id}/postmortem", summary="Generate or retrieve postmortem report")
@router.post("/{incident_id}/postmortem", summary="Generate or retrieve postmortem report")
def get_or_generate_postmortem(
    incident_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Generate comprehensive 15-section SRE postmortem in Markdown and JSON."""
    from automation.incident.postmortem.generator import PostmortemGenerator

    repo = IncidentRepository(db)
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )

    generator = PostmortemGenerator()
    return generator.generate(incident)
