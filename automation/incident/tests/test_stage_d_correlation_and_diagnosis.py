"""
Unit tests for Stage D: Alert Correlation and Automated Diagnosis Engines.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from automation.incident.api.main import app
from automation.incident.correlation.engine import AlertCorrelationEngine
from automation.incident.diagnosis.engine import AutomatedDiagnosisEngine
from automation.incident.models.database import IncidentBase, get_db
from automation.incident.models.incident import (
    ConfidenceLevel,
    Incident,
    IncidentSeverity,
    IncidentState,
)
from automation.incident.repositories.incident_repo import IncidentRepository
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


def test_correlation_downstream_dependency():
    engine = AlertCorrelationEngine(time_window_seconds=180)
    now = datetime.now(timezone.utc)

    # 1. Root incident on postgres
    root_inc = Incident(
        id="INC-ROOT-PG",
        title="PostgreSQL connection failures",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="postgres",
        source_alert="DatabaseConnectionFailures",
        created_at=now,
    )

    # 2. Downstream incident on API occurring 15 seconds later
    downstream_inc = Incident(
        id="INC-SYMPTOM-API",
        title="API high 5xx error rate",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="api",
        source_alert="APIHighErrorRate",
        created_at=now + timedelta(seconds=15),
    )

    # Correlate downstream incident against active incidents
    res = engine.correlate(downstream_inc, [root_inc, downstream_inc])
    assert res.is_root is False
    assert res.root_incident_id == "INC-ROOT-PG"
    assert res.relationship == "DOWNSTREAM_SYMPTOM"
    assert "depends on 'postgres'" in res.explanation

    # Correlate root incident against active incidents
    res_root = engine.correlate(root_inc, [root_inc, downstream_inc])
    assert res_root.is_root is True
    assert res_root.relationship == "ROOT_CAUSE"
    assert "INC-SYMPTOM-API" in res_root.correlated_incident_ids


def test_correlation_co_occurring():
    engine = AlertCorrelationEngine(time_window_seconds=180)
    now = datetime.now(timezone.utc)

    inc1 = Incident(
        id="INC-CPU",
        title="High CPU Utilization",
        severity=IncidentSeverity.WARNING.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="api",
        source_alert="HighCPUUtilization",
        created_at=now,
    )
    inc2 = Incident(
        id="INC-LATENCY",
        title="High Request Latency",
        severity=IncidentSeverity.WARNING.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="api",
        source_alert="HighRequestLatency",
        created_at=now + timedelta(seconds=20),
    )

    res = engine.correlate(inc1, [inc1, inc2])
    assert res.is_root is True
    assert res.relationship == "CO_OCCURRING"
    assert "INC-LATENCY" in res.correlated_incident_ids


def test_diagnosis_database_outage():
    diagnosis_engine = AutomatedDiagnosisEngine()
    incident = Incident(
        id="INC-TEST-DB-01",
        title="Database Down",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="postgres",
        source_alert="DatabaseConnectionFailures",
    )

    mock_evidence = {
        "deployments": {"postgres": {"desired": 0, "ready": 0, "available": 0}},
        "pods": [],
        "http_probes": {"/ready": {"status_code": 503, "body": {"database": "unreachable"}}},
        "active_chaos": [],
    }

    report = diagnosis_engine._synthesize(incident, mock_evidence)
    assert report.confidence == ConfidenceLevel.HIGH
    assert report.recommended_runbook == "database-unavailable"
    assert "PostgreSQL database failure" in report.suspected_cause
    assert len(report.recommended_actions) >= 1
    assert report.recommended_actions[0]["action_type"] == "scale_deployment"


def test_diagnosis_pod_crash_loop():
    diagnosis_engine = AutomatedDiagnosisEngine()
    incident = Incident(
        id="INC-TEST-CRASH-01",
        title="Worker CrashLoop",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="worker",
        source_alert="WorkerProcessingFailures",
    )

    mock_evidence = {
        "deployments": {"worker": {"desired": 1, "ready": 0}},
        "pods": [
            {
                "name": "worker-6b9c8d-xyz",
                "service": "worker",
                "phase": "Running",
                "ready": False,
                "restarts": 5,
                "waiting_reason": "CrashLoopBackOff",
                "exit_code": 1,
            }
        ],
        "http_probes": {},
        "active_chaos": [],
    }

    report = diagnosis_engine._synthesize(incident, mock_evidence)
    assert report.confidence == ConfidenceLevel.HIGH
    assert report.recommended_runbook == "pod-crash-loop"
    assert "crashlooping" in report.suspected_cause


def test_diagnose_api_endpoint(test_db):
    client, db = test_db
    repo = IncidentRepository(db)

    incident = Incident(
        id="INC-DIAG-001",
        title="API 5xx errors",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.TRIGGERED.value,
        affected_service="api",
        source_alert="APIHighErrorRate",
        fingerprint="fp-diag-test",
    )
    repo.create(incident)

    # Mock the internal collectors of AutomatedDiagnosisEngine so it runs deterministically
    with patch.object(
        AutomatedDiagnosisEngine,
        "_collect_pod_states",
        return_value=[{"name": "api-1", "service": "api", "ready": True, "restarts": 0}],
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_collect_deployment_states",
        return_value={"api": {"desired": 2, "ready": 2}, "postgres": {"desired": 1, "ready": 1}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_probe_endpoints",
        return_value={"/ready": {"status_code": 200, "body": {"status": "ready"}}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_check_chaos_mesh_experiments",
        return_value=[],
    ):
        resp = client.post("/api/incidents/INC-DIAG-001/diagnose")
        assert resp.status_code == 200
        data = resp.json()
        assert data["incident_id"] == "INC-DIAG-001"
        assert "suspected_cause" in data
        assert "confidence" in data
        assert "recommended_runbook" in data

    # Verify incident state was advanced to INVESTIGATING
    db.expire_all()
    updated_inc = repo.get_by_id("INC-DIAG-001")
    assert updated_inc.state == IncidentState.INVESTIGATING.value
    assert updated_inc.suspected_cause is not None

    # Verify timeline event was added
    timeline_repo = TimelineRepository(db)
    events = timeline_repo.list_events("INC-DIAG-001")
    event_types = [e.event_type for e in events]
    assert "STATE_TRANSITION_INVESTIGATING" in event_types
    assert "DIAGNOSIS_COMPLETED" in event_types
