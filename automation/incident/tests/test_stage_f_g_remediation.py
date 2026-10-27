"""
Unit tests for Stage F & G: Safe Remediation Policy Engine, Executor, and Human Approval Gate.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from automation.incident.api.main import app
from automation.incident.models.database import IncidentBase, get_db
from automation.incident.models.incident import (
    Incident,
    IncidentSeverity,
    IncidentState,
)
from automation.incident.models.remediation import (
    ApprovalDecision,
    ApprovalRequest,
    RemediationAction,
    RemediationRisk,
    RemediationState,
)
from automation.incident.remediation.executor import RemediationExecutor
from automation.incident.remediation.policy import (
    PolicyViolationError,
    RemediationPolicyEngine,
)
from automation.incident.repositories.incident_repo import IncidentRepository
from automation.incident.repositories.remediation_repo import RemediationRepository
from automation.incident.repositories.timeline_repo import TimelineRepository


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    IncidentBase.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    db = TestingSessionLocal()

    yield client, db

    app.dependency_overrides.clear()
    db.close()


def test_policy_engine_allowlist_and_safety_checks():
    policy = RemediationPolicyEngine(allowed_namespace="resiliencelab")

    # Valid action passes
    assert policy.validate_action(
        action_type="restart_deployment",
        target_resource="deploy/api",
        namespace="resiliencelab",
    ) is True

    # Disallowed action raises error
    with pytest.raises(PolicyViolationError, match="not in the permissible remediation allowlist"):
        policy.validate_action(
            action_type="delete_namespace",
            target_resource="resiliencelab",
            namespace="resiliencelab",
        )

    # Forbidden namespace raises error
    with pytest.raises(PolicyViolationError, match="Target namespace 'kube-system' is not permitted"):
        policy.validate_action(
            action_type="restart_deployment",
            target_resource="deploy/coredns",
            namespace="kube-system",
        )

    # PVC deletion attempt raises error
    with pytest.raises(PolicyViolationError, match="Destructive operation detected"):
        policy.validate_action(
            action_type="restart_deployment",
            target_resource="delete-pvc/orders-db-pvc",
            namespace="resiliencelab",
        )

    # Scale bounds: scaling to 0 or >4 is rejected
    with pytest.raises(PolicyViolationError, match="violates bounds"):
        policy.validate_action(
            action_type="scale_deployment",
            target_resource="deploy/api",
            namespace="resiliencelab",
            parameters={"replicas": 0},
        )
    with pytest.raises(PolicyViolationError, match="violates bounds"):
        policy.validate_action(
            action_type="scale_deployment",
            target_resource="deploy/api",
            namespace="resiliencelab",
            parameters={"replicas": 5},
        )

    # Scale within bounds (1-4) passes
    assert policy.validate_action(
        action_type="scale_deployment",
        target_resource="deploy/api",
        namespace="resiliencelab",
        parameters={"replicas": 3},
    ) is True


def test_executor_dry_run_and_approval_enforcement():
    executor = RemediationExecutor(default_dry_run=True)

    # Low risk action executes in dry-run mode
    low_risk = RemediationAction(
        id="REM-DRY-01",
        incident_id="INC-001",
        action_type="restart_deployment",
        target_resource="deploy/api",
        namespace="resiliencelab",
        risk_level=RemediationRisk.LOW.value,
        requires_approval=False,
    )
    res = executor.execute(low_risk)
    assert res.success is True
    assert res.dry_run is True
    assert "[DRY-RUN SIMULATION]" in res.output

    # High risk action without approval is rejected
    high_risk = RemediationAction(
        id="REM-DRY-02",
        incident_id="INC-001",
        action_type="scale_deployment",
        target_resource="deploy/postgres",
        namespace="resiliencelab",
        parameters={"replicas": 1},
        risk_level=RemediationRisk.HIGH.value,
        requires_approval=True,
    )
    with pytest.raises(PolicyViolationError, match="requires human approval"):
        executor.execute(high_risk, approval=None)

    # High risk action with approved approval executes in dry-run
    approval = ApprovalRequest(
        id="APP-001",
        incident_id="INC-001",
        remediation_id="REM-DRY-02",
        action="scale postgres",
        reason="db recovery",
        risk_level=RemediationRisk.HIGH.value,
        decision=ApprovalDecision.APPROVED.value,
        approver="sre_oncall",
        expires_at=datetime.now(timezone.utc),
    )
    res2 = executor.execute(high_risk, approval=approval)
    assert res2.success is True
    assert res2.dry_run is True


def test_remediate_and_approval_api_flow(test_db):
    client, db = test_db
    repo = IncidentRepository(db)

    # Create postgres incident
    incident = Incident(
        id="INC-REM-001",
        title="PostgreSQL Down",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.INVESTIGATING.value,
        affected_service="postgres",
        source_alert="DatabaseConnectionFailures",
        fingerprint="fp-rem-pg",
    )
    repo.create(incident)

    # Trigger remediation API in dry-run mode
    resp = client.post("/api/incidents/INC-REM-001/remediate", json={"dry_run": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["incident_id"] == "INC-REM-001"
    assert data["runbook_id"] == "database-unavailable"
    assert data["approval_required"] is True
    assert len(data["pending_approvals"]) == 1

    app_id = data["pending_approvals"][0]["id"]

    # Verify incident state is now REMEDIATING
    db.expire_all()
    inc = repo.get_by_id("INC-REM-001")
    assert inc.state == IncidentState.REMEDIATING.value

    # Approve remediation via API
    approve_resp = client.post(
        "/api/incidents/INC-REM-001/approve-remediation",
        json={
            "approval_id": app_id,
            "decision": "APPROVED",
            "approver": "sre_lead",
            "reason": "Recovery validated",
            "dry_run": True,
        },
    )
    assert approve_resp.status_code == 200
    app_data = approve_resp.json()
    assert app_data["status"] == "executed"
    assert app_data["approval"]["decision"] == ApprovalDecision.APPROVED.value
    assert app_data["execution_result"]["success"] is True

    # Verify incident state has advanced to VERIFYING!
    db.expire_all()
    inc_after = repo.get_by_id("INC-REM-001")
    assert inc_after.state == IncidentState.VERIFYING.value

    # Verify timeline recorded APPROVAL_GRANTED and REMEDIATION_DRY_RUN
    timeline_repo = TimelineRepository(db)
    events = timeline_repo.list_events("INC-REM-001")
    event_types = [e.event_type for e in events]
    assert "APPROVAL_REQUESTED" in event_types
    assert "APPROVAL_GRANTED" in event_types
    assert "REMEDIATION_DRY_RUN" in event_types
    assert "STATE_TRANSITION_VERIFYING" in event_types


def test_remediation_rejection_escalation(test_db):
    client, db = test_db
    repo = IncidentRepository(db)

    incident = Incident(
        id="INC-REM-002",
        title="PostgreSQL Down Rejection Test",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.INVESTIGATING.value,
        affected_service="postgres",
        source_alert="DatabaseConnectionFailures",
        fingerprint="fp-rem-pg-2",
    )
    repo.create(incident)

    # Trigger remediation
    resp = client.post("/api/incidents/INC-REM-002/remediate", json={"dry_run": True})
    app_id = resp.json()["pending_approvals"][0]["id"]

    # Reject remediation
    reject_resp = client.post(
        "/api/incidents/INC-REM-002/approve-remediation",
        json={
            "approval_id": app_id,
            "decision": "REJECTED",
            "approver": "sre_lead",
            "reason": "Maintenance in progress, do not auto-scale",
        },
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    # Incident state must be ESCALATED
    db.expire_all()
    inc = repo.get_by_id("INC-REM-002")
    assert inc.state == IncidentState.ESCALATED.value
