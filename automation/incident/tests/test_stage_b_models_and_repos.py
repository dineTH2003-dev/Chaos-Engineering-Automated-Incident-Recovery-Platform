"""
Unit tests for Stage B: Models, Repositories, and Lifecycle State Machine.
"""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from automation.incident.models.database import IncidentBase
from automation.incident.models.incident import (
    Incident,
    IncidentAlert,
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
from automation.incident.repositories.incident_repo import IncidentRepository
from automation.incident.repositories.remediation_repo import RemediationRepository
from automation.incident.repositories.timeline_repo import TimelineRepository
from automation.incident.services.lifecycle_service import (
    IncidentLifecycleService,
    InvalidStateTransitionError,
)


@pytest.fixture
def db_session():
    """Create an isolated in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    IncidentBase.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_incident_creation_and_retrieval(db_session):
    repo = IncidentRepository(db_session)
    incident = Incident(
        id="INC-20261027-001",
        title="API Service Unavailable",
        description="High 5xx error rate detected",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="api",
        namespace="resiliencelab",
        fingerprint="fp-api-down-12345",
        source_alert="APIHighErrorRate",
    )
    created = repo.create(incident)
    assert created.id == "INC-20261027-001"

    # Fetch by ID
    fetched = repo.get_by_id("INC-20261027-001")
    assert fetched is not None
    assert fetched.affected_service == "api"
    assert fetched.state == IncidentState.TRIGGERED.value

    # Fetch active by fingerprint
    active = repo.get_active_by_fingerprint("fp-api-down-12345")
    assert active is not None
    assert active.id == "INC-20261027-001"


def test_timeline_recording(db_session):
    incident_repo = IncidentRepository(db_session)
    timeline_repo = TimelineRepository(db_session)

    incident = Incident(
        id="INC-20261027-002",
        title="PostgreSQL Failure",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="postgres",
        namespace="resiliencelab",
        fingerprint="fp-pg-down-999",
        source_alert="DatabaseConnectionFailures",
    )
    incident_repo.create(incident)

    event1 = timeline_repo.record_event(
        incident_id="INC-20261027-002",
        event_type="ALERT_INGESTED",
        summary="Received alert from Alertmanager",
        details={"alertname": "DatabaseConnectionFailures"},
        actor="ALERTMANAGER",
    )
    assert event1.id is not None

    events = timeline_repo.list_events("INC-20261027-002")
    assert len(events) == 1
    assert events[0].event_type == "ALERT_INGESTED"


def test_lifecycle_state_transitions(db_session):
    incident_repo = IncidentRepository(db_session)
    timeline_repo = TimelineRepository(db_session)
    lifecycle = IncidentLifecycleService(db_session)

    incident = Incident(
        id="INC-20261027-003",
        title="Worker Failure",
        severity=IncidentSeverity.WARNING.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="worker",
        namespace="resiliencelab",
        fingerprint="fp-worker-456",
        source_alert="WorkerProcessingFailures",
    )
    incident_repo.create(incident)

    # Valid transition: TRIGGERED -> INVESTIGATING
    lifecycle.start_investigation("INC-20261027-003", actor="AUTO_DIAGNOSIS", reason="Correlation matched")
    inc = incident_repo.get_by_id("INC-20261027-003")
    assert inc.state == IncidentState.INVESTIGATING.value

    # Valid transition: INVESTIGATING -> REMEDIATING
    lifecycle.start_remediation("INC-20261027-003", actor="REMEDIATION_ENGINE", reason="Restarting worker pod")
    inc = incident_repo.get_by_id("INC-20261027-003")
    assert inc.state == IncidentState.REMEDIATING.value

    # Valid transition: REMEDIATING -> VERIFYING
    lifecycle.start_verification("INC-20261027-003", actor="VERIFICATION_ENGINE", reason="Probing pod readiness")
    inc = incident_repo.get_by_id("INC-20261027-003")
    assert inc.state == IncidentState.VERIFYING.value

    # Valid transition: VERIFYING -> RESOLVED
    lifecycle.resolve("INC-20261027-003", actor="VERIFICATION_ENGINE", reason="All probes passed")
    inc = incident_repo.get_by_id("INC-20261027-003")
    assert inc.state == IncidentState.RESOLVED.value
    assert inc.resolved_at is not None

    # Verify timeline events were created automatically
    events = timeline_repo.list_events("INC-20261027-003")
    assert len(events) == 4
    event_types = [e.event_type for e in events]
    assert event_types == [
        "STATE_TRANSITION_INVESTIGATING",
        "STATE_TRANSITION_REMEDIATING",
        "STATE_TRANSITION_VERIFYING",
        "STATE_TRANSITION_RESOLVED",
    ]


def test_invalid_state_transition(db_session):
    incident_repo = IncidentRepository(db_session)
    lifecycle = IncidentLifecycleService(db_session)

    incident = Incident(
        id="INC-20261027-004",
        title="Invalid Transition Test",
        severity=IncidentSeverity.INFO.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="api",
        namespace="resiliencelab",
        fingerprint="fp-test-004",
        source_alert="TestAlert",
    )
    incident_repo.create(incident)

    # Invalid: TRIGGERED -> RESOLVED (cannot jump straight to resolved without investigation/verification)
    with pytest.raises(InvalidStateTransitionError):
        lifecycle.resolve("INC-20261027-004")


def test_remediation_and_approval_flow(db_session):
    incident_repo = IncidentRepository(db_session)
    remediation_repo = RemediationRepository(db_session)

    incident = Incident(
        id="INC-20261027-005",
        title="Postgres Scale Down",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.INVESTIGATING.value,
        affected_service="postgres",
        namespace="resiliencelab",
        fingerprint="fp-test-005",
        source_alert="DatabaseDown",
    )
    incident_repo.create(incident)

    # High-risk action requiring approval
    remediation = RemediationAction(
        id="REM-001",
        incident_id="INC-20261027-005",
        runbook_id="database-recovery",
        action_type="scale_deployment",
        target_resource="deploy/postgres",
        namespace="resiliencelab",
        parameters={"replicas": 1},
        risk_level=RemediationRisk.HIGH.value,
        requires_approval=True,
        state=RemediationState.PENDING_APPROVAL.value,
        dry_run=False,
    )
    remediation_repo.create_remediation(remediation)

    approval = ApprovalRequest(
        id="APP-001",
        incident_id="INC-20261027-005",
        remediation_id="REM-001",
        action="Scale postgres to 1 replica",
        reason="Recover database from 0 replicas",
        risk_level=RemediationRisk.HIGH.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    remediation_repo.create_approval(approval)

    pending = remediation_repo.get_pending_approval_for_incident("INC-20261027-005")
    assert pending is not None
    assert pending.id == "APP-001"

    # Human decides approval
    decided = remediation_repo.decide_approval(
        approval_id="APP-001",
        decision=ApprovalDecision.APPROVED,
        approver="senior_sre",
        reason="Recovery confirmed necessary",
    )
    assert decided.decision == ApprovalDecision.APPROVED.value
    assert decided.approver == "senior_sre"

    # Now pending should be None
    assert remediation_repo.get_pending_approval_for_incident("INC-20261027-005") is None
