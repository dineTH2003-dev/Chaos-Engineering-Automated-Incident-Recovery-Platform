"""
Unit tests for Stage C: Alert Ingestion, Normalization, Deduplication, and API Endpoints.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from automation.incident.api.main import app
from automation.incident.models.database import IncidentBase, get_db
from automation.incident.models.incident import IncidentState
from automation.incident.repositories.incident_repo import IncidentRepository
from sqlalchemy.pool import StaticPool
from automation.incident.repositories.timeline_repo import TimelineRepository
from automation.incident.services.ingestion_service import (
    AlertIngestionService,
    clear_raw_alerts_buffer,
    get_raw_alerts_buffer,
)


@pytest.fixture
def test_db():
    """Create an isolated in-memory SQLite database and test client."""
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
    clear_raw_alerts_buffer()

    client = TestClient(app)
    db = TestingSessionLocal()

    yield client, db

    app.dependency_overrides.clear()
    clear_raw_alerts_buffer()
    db.close()


def test_post_alerts_creates_incident(test_db):
    client, db = test_db
    payload = {
        "receiver": "resiliencelab-webhook",
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
                    "summary": "High 5xx error rate on API service",
                    "description": "API 5xx error rate is 25% over last 1m",
                },
                "startsAt": "2026-10-27T14:00:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": "fp-api-high-error-rate-1",
            }
        ],
    }

    response = client.post("/alerts", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    assert data["alerts_processed"] == 1
    assert len(data["affected_incidents"]) == 1

    incident_id = data["affected_incidents"][0]
    repo = IncidentRepository(db)
    incident = repo.get_by_id(incident_id)
    assert incident is not None
    assert incident.affected_service == "api"
    assert incident.severity == "critical"
    assert incident.state == IncidentState.TRIGGERED.value
    assert incident.source_alert == "APIHighErrorRate"
    assert len(incident.alerts) == 1

    timeline_repo = TimelineRepository(db)
    events = timeline_repo.list_events(incident_id)
    event_types = [e.event_type for e in events]
    assert "INCIDENT_TRIGGERED" in event_types
    assert "ALERT_RECEIVED" in event_types


def test_alert_deduplication(test_db):
    client, db = test_db
    alert_payload = {
        "status": "firing",
        "labels": {
            "alertname": "DatabaseConnectionFailures",
            "severity": "critical",
            "service": "postgres",
            "namespace": "resiliencelab",
        },
        "annotations": {
            "summary": "PostgreSQL database connections failing",
            "description": "API cannot connect to PostgreSQL",
        },
        "startsAt": "2026-10-27T14:05:00Z",
        "fingerprint": "fp-pg-fail-dedup",
    }
    payload = {"receiver": "resiliencelab-webhook", "status": "firing", "alerts": [alert_payload]}

    # First delivery
    resp1 = client.post("/alerts", json=payload)
    assert resp1.status_code == 200
    inc_id_1 = resp1.json()["affected_incidents"][0]

    # Second delivery with same fingerprint (subsequent notification from Alertmanager)
    resp2 = client.post("/alerts", json=payload)
    assert resp2.status_code == 200
    inc_id_2 = resp2.json()["affected_incidents"][0]

    # Must deduplicate to the same incident!
    assert inc_id_1 == inc_id_2

    repo = IncidentRepository(db)
    incident = repo.get_by_id(inc_id_1)
    # Alerts list should now have 2 alerts
    assert len(incident.alerts) == 2

    # Check timeline has ALERT_DEDUPLICATED event
    timeline_repo = TimelineRepository(db)
    events = timeline_repo.list_events(inc_id_1)
    event_types = [e.event_type for e in events]
    assert "ALERT_DEDUPLICATED" in event_types


def test_resolved_alert_handling(test_db):
    client, db = test_db
    fingerprint = "fp-worker-resolved-test"
    firing_payload = {
        "receiver": "resiliencelab-webhook",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "WorkerProcessingFailures",
                    "severity": "warning",
                    "service": "worker",
                    "namespace": "resiliencelab",
                },
                "annotations": {"summary": "Worker failed to poll orders"},
                "startsAt": "2026-10-27T14:10:00Z",
                "fingerprint": fingerprint,
            }
        ],
    }
    resp1 = client.post("/alerts", json=firing_payload)
    inc_id = resp1.json()["affected_incidents"][0]

    # Send resolution payload
    resolved_payload = {
        "receiver": "resiliencelab-webhook",
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",
                "labels": {
                    "alertname": "WorkerProcessingFailures",
                    "severity": "warning",
                    "service": "worker",
                    "namespace": "resiliencelab",
                },
                "annotations": {"summary": "Worker recovered"},
                "startsAt": "2026-10-27T14:10:00Z",
                "endsAt": "2026-10-27T14:15:00Z",
                "fingerprint": fingerprint,
            }
        ],
    }
    resp2 = client.post("/alerts", json=resolved_payload)
    assert resp2.status_code == 200

    repo = IncidentRepository(db)
    incident = repo.get_by_id(inc_id)
    # Incident must NOT be prematurely marked RESOLVED without verification!
    assert incident.state == IncidentState.TRIGGERED.value

    # Timeline should record ALERT_RESOLVED
    timeline_repo = TimelineRepository(db)
    events = timeline_repo.list_events(inc_id)
    event_types = [e.event_type for e in events]
    assert "ALERT_RESOLVED" in event_types


def test_incidents_api_routes(test_db):
    client, db = test_db
    # Ingest an alert to create an incident
    payload = {
        "receiver": "resiliencelab-webhook",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPUUtilization",
                    "severity": "warning",
                    "service": "api",
                },
                "annotations": {"summary": "API CPU above 85%"},
                "fingerprint": "fp-cpu-stress-test",
            }
        ],
    }
    resp = client.post("/alerts", json=payload)
    inc_id = resp.json()["affected_incidents"][0]

    # GET /api/incidents
    list_resp = client.get("/api/incidents")
    assert list_resp.status_code == 200
    incidents = list_resp.json()
    assert len(incidents) >= 1
    assert any(i["id"] == inc_id for i in incidents)

    # GET /api/incidents/{id}
    detail_resp = client.get(f"/api/incidents/{inc_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == inc_id
    assert "alerts" in detail
    assert "timeline" in detail

    # POST /api/incidents/{id}/acknowledge
    ack_resp = client.post(
        f"/api/incidents/{inc_id}/acknowledge",
        json={"actor": "sre_engineer", "reason": "Investigating CPU spike"},
    )
    assert ack_resp.status_code == 200
    assert ack_resp.json()["state"] == IncidentState.ACKNOWLEDGED.value


def test_legacy_alerts_buffer_and_clear(test_db):
    client, _ = test_db
    # Verify buffer starts empty or contains sent alerts
    resp = client.get("/alerts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    # Clear buffer
    del_resp = client.delete("/alerts")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "cleared"

    resp2 = client.get("/alerts")
    assert len(resp2.json()) == 0
