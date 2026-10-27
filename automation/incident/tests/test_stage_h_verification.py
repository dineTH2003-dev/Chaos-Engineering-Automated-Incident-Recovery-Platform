"""
Unit tests for Stage H: Multi-Step Recovery Verification Engine.
"""

from unittest.mock import MagicMock, patch
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
from automation.incident.repositories.incident_repo import IncidentRepository
from automation.incident.repositories.timeline_repo import TimelineRepository
from automation.incident.verification.engine import (
    ProbeCheckResult,
    RecoveryVerificationEngine,
)


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


def test_verification_engine_healthy():
    verifier = RecoveryVerificationEngine(
        consecutive_success_required=3,
        check_interval_seconds=0.01,
    )
    incident = Incident(
        id="INC-VERIFY-001",
        title="Test Recovery",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.REMEDIATING.value,
        affected_service="api",
        source_alert="APIHighErrorRate",
    )

    with patch.object(
        verifier,
        "_verify_deployment_readiness",
        return_value={"healthy": True, "desired": 2, "ready": 2},
    ), patch.object(
        verifier,
        "_probe_health_and_readiness",
        side_effect=[
            ProbeCheckResult(1, "2026-10-27T14:00:00Z", 200, 200, {"status": "ready", "database": "connected"}, True),
            ProbeCheckResult(2, "2026-10-27T14:00:01Z", 200, 200, {"status": "ready", "database": "connected"}, True),
            ProbeCheckResult(3, "2026-10-27T14:00:02Z", 200, 200, {"status": "ready", "database": "connected"}, True),
        ],
    ), patch.object(
        verifier,
        "_verify_telemetry",
        return_value={"healthy": True, "promql_error_rate": 0.0},
    ):
        report = verifier.verify_recovery(incident)
        assert report.is_healthy is True
        assert report.status == "VERIFIED_RECOVERED"
        assert report.consecutive_successes == 3


def test_verification_engine_unready_deployment():
    verifier = RecoveryVerificationEngine(
        consecutive_success_required=3,
        check_interval_seconds=0.01,
    )
    incident = Incident(
        id="INC-VERIFY-002",
        title="Postgres Outage",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.REMEDIATING.value,
        affected_service="postgres",
        source_alert="DatabaseConnectionFailures",
    )

    with patch.object(
        verifier,
        "_verify_deployment_readiness",
        return_value={"healthy": False, "desired": 1, "ready": 0, "reason": "0/1 replicas ready"},
    ):
        report = verifier.verify_recovery(incident)
        assert report.is_healthy is False
        assert report.status == "VERIFICATION_FAILED"


def test_verification_engine_probe_failure():
    verifier = RecoveryVerificationEngine(
        consecutive_success_required=3,
        check_interval_seconds=0.01,
    )
    incident = Incident(
        id="INC-VERIFY-003",
        title="Probe Failing",
        severity=IncidentSeverity.WARNING.value,
        state=IncidentState.REMEDIATING.value,
        affected_service="api",
        source_alert="APIHighErrorRate",
    )

    with patch.object(
        verifier,
        "_verify_deployment_readiness",
        return_value={"healthy": True, "desired": 2, "ready": 2},
    ), patch.object(
        verifier,
        "_probe_health_and_readiness",
        side_effect=[
            ProbeCheckResult(1, "2026-10-27T14:00:00Z", 200, 200, {"status": "ready"}, True),
            ProbeCheckResult(2, "2026-10-27T14:00:01Z", 200, 503, {"database": "unreachable"}, False),
            ProbeCheckResult(3, "2026-10-27T14:00:02Z", 200, 200, {"status": "ready"}, True),
        ],
    ), patch.object(
        verifier,
        "_verify_telemetry",
        return_value={"healthy": True},
    ):
        report = verifier.verify_recovery(incident)
        assert report.is_healthy is False
        assert report.status == "VERIFICATION_FAILED"


def test_verify_api_endpoint(test_db):
    client, db = test_db
    repo = IncidentRepository(db)

    incident = Incident(
        id="INC-VERIFY-004",
        title="API Service Outage",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.REMEDIATING.value,
        affected_service="api",
        source_alert="APIHighErrorRate",
        fingerprint="fp-verify-api",
    )
    repo.create(incident)

    with patch.object(
        RecoveryVerificationEngine,
        "_verify_deployment_readiness",
        return_value={"healthy": True, "desired": 2, "ready": 2},
    ), patch.object(
        RecoveryVerificationEngine,
        "_probe_health_and_readiness",
        return_value=ProbeCheckResult(1, "2026-10-27T14:00:00Z", 200, 200, {"status": "ready"}, True),
    ), patch.object(
        RecoveryVerificationEngine,
        "_verify_telemetry",
        return_value={"healthy": True},
    ):
        resp = client.post("/api/incidents/INC-VERIFY-004/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_healthy"] is True
        assert data["status"] == "VERIFIED_RECOVERED"

    # Verify incident state was updated to RESOLVED in database
    db.expire_all()
    resolved_inc = repo.get_by_id("INC-VERIFY-004")
    assert resolved_inc.state == IncidentState.RESOLVED.value
    assert resolved_inc.resolved_at is not None

    # Verify timeline event was recorded
    timeline_repo = TimelineRepository(db)
    events = timeline_repo.list_events("INC-VERIFY-004")
    event_types = [e.event_type for e in events]
    assert "STATE_TRANSITION_RESOLVED" in event_types
    assert "RECOVERY_VERIFIED" in event_types
