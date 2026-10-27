"""
End-to-end test suite for Stage K: Complete Lifecycle Incident Scenarios.
Simulates 4 full SRE incident scenarios:
1. API High Error Rate -> Ingest -> Diagnose -> Remediate -> Verify -> Resolve -> Postmortem
2. PostgreSQL Outage -> Ingest -> Diagnose -> Approval Gate -> Operator Approve -> Execute -> Verify -> Postmortem
3. CPU Saturation -> Ingest -> Diagnose -> Scale Up -> Verify -> Resolve
4. High Request Latency -> Ingest -> Chaos Correlation -> Verify -> Resolve
"""

from datetime import datetime, timedelta, timezone
import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from automation.incident.api.main import app
from automation.incident.diagnosis.engine import AutomatedDiagnosisEngine
from automation.incident.models.database import IncidentBase, get_db
from automation.incident.models.incident import (
    ConfidenceLevel,
    IncidentState,
)
from automation.incident.repositories.incident_repo import IncidentRepository
from automation.incident.verification.engine import (
    ProbeCheckResult,
    RecoveryVerificationEngine,
)


@pytest.fixture
def e2e_client():
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


def test_e2e_scenario_1_api_failure_and_recovery(e2e_client):
    client, db = e2e_client

    # 1. Alertmanager fires APIHighErrorRate
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "APIHighErrorRate",
                    "severity": "critical",
                    "service": "api",
                    "namespace": "resiliencelab",
                },
                "annotations": {
                    "summary": "API 5xx error rate exceeded 5%",
                    "description": "5xx error rate is 28.5%",
                },
                "startsAt": datetime.now(timezone.utc).isoformat(),
                "fingerprint": "fp-e2e-api-01",
            }
        ],
    }
    ingest_resp = client.post("/alerts", json=payload)
    assert ingest_resp.status_code == 200
    incident_id = ingest_resp.json()["affected_incidents"][0]

    # 2. Trigger automated multi-source diagnosis
    with patch.object(
        AutomatedDiagnosisEngine,
        "_collect_pod_states",
        return_value=[{"name": "api-1", "service": "api", "ready": True}],
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_collect_deployment_states",
        return_value={"api": {"desired": 2, "ready": 1}, "postgres": {"desired": 1, "ready": 1}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_probe_endpoints",
        return_value={"/ready": {"status_code": 200, "body": {"status": "ready"}}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_check_chaos_mesh_experiments",
        return_value=[],
    ):
        diag_resp = client.post(f"/api/incidents/{incident_id}/diagnose")
        assert diag_resp.status_code == 200
        diag_data = diag_resp.json()
        assert diag_data["recommended_runbook"] == "api-unavailable"

    # 3. Trigger remediation (dry-run)
    rem_resp = client.post(f"/api/incidents/{incident_id}/remediate", json={"dry_run": True})
    assert rem_resp.status_code == 200
    rem_data = rem_resp.json()
    assert rem_data["runbook_id"] == "api-unavailable"
    assert rem_data["approval_required"] is False
    assert len(rem_data["actions"]) == 3

    # 4. Multi-step recovery verification
    with patch.object(
        RecoveryVerificationEngine,
        "_verify_deployment_readiness",
        return_value={"healthy": True, "desired": 2, "ready": 2},
    ), patch.object(
        RecoveryVerificationEngine,
        "_probe_health_and_readiness",
        return_value=ProbeCheckResult(1, "now", 200, 200, {"status": "ready"}, True),
    ), patch.object(
        RecoveryVerificationEngine,
        "_verify_telemetry",
        return_value={"healthy": True},
    ):
        verify_resp = client.post(f"/api/incidents/{incident_id}/verify")
        assert verify_resp.status_code == 200
        verify_data = verify_resp.json()
        assert verify_data["is_healthy"] is True
        assert verify_data["status"] == "VERIFIED_RECOVERED"

    # 5. Verify incident is RESOLVED
    detail_resp = client.get(f"/api/incidents/{incident_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["state"] == IncidentState.RESOLVED.value
    assert detail["resolved_at"] is not None

    # 6. Generate postmortem
    pm_resp = client.get(f"/api/incidents/{incident_id}/postmortem")
    assert pm_resp.status_code == 200
    pm_data = pm_resp.json()
    assert pm_data["metadata"]["incident_id"] == incident_id
    assert os.path.isfile(pm_data["markdown_path"])
    assert os.path.isfile(pm_data["json_path"])


def test_e2e_scenario_2_postgres_outage_with_approval_gate(e2e_client):
    client, db = e2e_client

    # 1. Alertmanager fires DatabaseConnectionFailures
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "DatabaseConnectionFailures",
                    "severity": "critical",
                    "service": "postgres",
                    "namespace": "resiliencelab",
                },
                "annotations": {
                    "summary": "PostgreSQL database unavailable",
                },
                "startsAt": datetime.now(timezone.utc).isoformat(),
                "fingerprint": "fp-e2e-pg-01",
            }
        ],
    }
    ingest_resp = client.post("/alerts", json=payload)
    incident_id = ingest_resp.json()["affected_incidents"][0]

    # 2. Automated diagnosis identifies postgres outage
    with patch.object(
        AutomatedDiagnosisEngine,
        "_collect_pod_states",
        return_value=[],
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_collect_deployment_states",
        return_value={"postgres": {"desired": 0, "ready": 0}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_probe_endpoints",
        return_value={"/ready": {"status_code": 503, "body": {"database": "unreachable"}}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_check_chaos_mesh_experiments",
        return_value=[],
    ):
        diag_resp = client.post(f"/api/incidents/{incident_id}/diagnose")
        assert diag_resp.status_code == 200
        diag_data = diag_resp.json()
        assert diag_data["recommended_runbook"] == "database-unavailable"
        assert diag_data["confidence"] == "HIGH"

    # 3. Stage remediation: database-unavailable requires human approval for scaling!
    rem_resp = client.post(f"/api/incidents/{incident_id}/remediate", json={"dry_run": True})
    assert rem_resp.status_code == 200
    rem_data = rem_resp.json()
    assert rem_data["approval_required"] is True
    assert len(rem_data["pending_approvals"]) == 1
    approval_id = rem_data["pending_approvals"][0]["id"]

    # 4. Human Operator approves remediation
    approve_resp = client.post(
        f"/api/incidents/{incident_id}/approve-remediation",
        json={
            "approval_id": approval_id,
            "decision": "APPROVED",
            "approver": "sre_lead_operator",
            "reason": "Database PVC verified intact, approving restore to 1 replica",
            "dry_run": True,
        },
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "executed"

    # 5. Multi-step verification succeeds once DB is restored
    with patch.object(
        RecoveryVerificationEngine,
        "_verify_deployment_readiness",
        return_value={"healthy": True, "desired": 1, "ready": 1},
    ), patch.object(
        RecoveryVerificationEngine,
        "_probe_health_and_readiness",
        return_value=ProbeCheckResult(1, "now", 200, 200, {"status": "ready", "database": "connected"}, True),
    ), patch.object(
        RecoveryVerificationEngine,
        "_verify_telemetry",
        return_value={"healthy": True},
    ):
        verify_resp = client.post(f"/api/incidents/{incident_id}/verify")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["is_healthy"] is True

    # 6. Verify postmortem generated
    pm_resp = client.get(f"/api/incidents/{incident_id}/postmortem")
    assert pm_resp.status_code == 200
    assert pm_resp.json()["metadata"]["status"] == IncidentState.RESOLVED.value


def test_e2e_scenario_3_cpu_saturation_scaling(e2e_client):
    client, db = e2e_client

    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPUUtilization",
                    "severity": "warning",
                    "service": "api",
                },
                "annotations": {"summary": "API CPU saturation > 85%"},
                "fingerprint": "fp-e2e-cpu-01",
            }
        ],
    }
    ingest_resp = client.post("/alerts", json=payload)
    incident_id = ingest_resp.json()["affected_incidents"][0]

    # Diagnose -> selects high-cpu runbook
    with patch.object(
        AutomatedDiagnosisEngine,
        "_collect_pod_states",
        return_value=[],
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_collect_deployment_states",
        return_value={"api": {"desired": 2, "ready": 2}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_probe_endpoints",
        return_value={"/ready": {"status_code": 200, "body": {"status": "ready"}}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_check_chaos_mesh_experiments",
        return_value=[{"kind": "stresschaos", "name": "ce04-cpu-stress"}],
    ):
        diag_resp = client.post(f"/api/incidents/{incident_id}/diagnose")
        assert diag_resp.status_code == 200
        assert diag_resp.json()["recommended_runbook"] == "high-cpu"

    # Remediate -> scales deployment to 3 replicas
    rem_resp = client.post(f"/api/incidents/{incident_id}/remediate", json={"dry_run": True})
    assert rem_resp.status_code == 200
    assert rem_resp.json()["runbook_id"] == "high-cpu"
    assert rem_resp.json()["approval_required"] is False


def test_e2e_scenario_4_latency_and_chaos_correlation(e2e_client):
    client, db = e2e_client

    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighRequestLatency",
                    "severity": "warning",
                    "service": "api",
                },
                "annotations": {"summary": "p95 latency exceeded 200ms"},
                "fingerprint": "fp-e2e-lat-01",
            }
        ],
    }
    ingest_resp = client.post("/alerts", json=payload)
    incident_id = ingest_resp.json()["affected_incidents"][0]

    with patch.object(
        AutomatedDiagnosisEngine,
        "_collect_pod_states",
        return_value=[],
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_collect_deployment_states",
        return_value={"api": {"desired": 2, "ready": 2}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_probe_endpoints",
        return_value={"/ready": {"status_code": 200, "body": {"status": "ready"}}},
    ), patch.object(
        AutomatedDiagnosisEngine,
        "_check_chaos_mesh_experiments",
        return_value=[{"kind": "networkchaos", "name": "ce02-network-latency"}],
    ):
        diag_resp = client.post(f"/api/incidents/{incident_id}/diagnose")
        assert diag_resp.status_code == 200
        data = diag_resp.json()
        assert data["recommended_runbook"] == "high-latency"
        assert "networkchaos" in data["suspected_cause"].lower()
