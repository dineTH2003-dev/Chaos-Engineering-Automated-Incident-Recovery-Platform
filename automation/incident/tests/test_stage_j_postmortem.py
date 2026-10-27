"""
Unit tests for Stage J: Incident Timeline, Postmortem Generation, and Dashboard 6.
"""

from datetime import datetime, timedelta, timezone
import os
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
    RemediationAction,
    RemediationRisk,
    RemediationState,
)
from automation.incident.models.timeline import IncidentTimeline
from automation.incident.postmortem.generator import PostmortemGenerator
from automation.incident.repositories.incident_repo import IncidentRepository


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


def test_postmortem_generator_15_sections(tmp_path):
    generator = PostmortemGenerator(output_dir=str(tmp_path))

    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=120)

    incident = Incident(
        id="INC-PM-001",
        title="PostgreSQL Outage & Recovery",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.RESOLVED.value,
        affected_service="postgres",
        source_alert="DatabaseConnectionFailures",
        suspected_cause="PostgreSQL deployment scaled to 0",
        confidence="HIGH",
        created_at=start,
        resolved_at=now,
    )

    ev1 = IncidentTimeline(
        incident_id="INC-PM-001",
        timestamp=start + timedelta(seconds=5),
        event_type="ALERT_RECEIVED",
        summary="Alert received from Alertmanager",
        actor="ALERTMANAGER",
    )
    ev2 = IncidentTimeline(
        incident_id="INC-PM-001",
        timestamp=start + timedelta(seconds=20),
        event_type="REMEDIATION_EXECUTED",
        summary="Scaled postgres to 1 replica",
        actor="SRE_LEAD",
    )
    incident.timeline = [ev1, ev2]

    rem1 = RemediationAction(
        id="REM-PM-01",
        incident_id="INC-PM-001",
        action_type="scale_deployment",
        target_resource="deploy/postgres",
        namespace="resiliencelab",
        risk_level=RemediationRisk.HIGH.value,
        requires_approval=True,
        state=RemediationState.COMPLETED.value,
        dry_run=False,
    )
    incident.remediations = [rem1]

    report = generator.generate(incident)
    assert report["metadata"]["incident_id"] == "INC-PM-001"
    assert report["durations"]["total_outage_seconds"] == 120.0
    assert report["durations"]["ttd_seconds"] == 5.0
    assert len(report["timeline"]) == 2

    # Verify JSON file exists
    json_path = os.path.join(tmp_path, "INC-PM-001.json")
    assert os.path.isfile(json_path)

    # Verify Markdown file exists and contains essential SRE sections
    md_path = os.path.join(tmp_path, "INC-PM-001.md")
    assert os.path.isfile(md_path)
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    assert "# Postmortem: INC-PM-001" in md_text
    assert "## 1. Incident Overview" in md_text
    assert "## 2. Executive Summary" in md_text
    assert "## 3. Impact Assessment" in md_text
    assert "## 4. SRE Key Metrics Duration Table" in md_text
    assert "## 5. Chronological Incident Timeline" in md_text
    assert "## 6. Root Cause Analysis (5 Whys)" in md_text
    assert "## 15. Sign-off & Audit Trail" in md_text


def test_postmortem_api_endpoint(test_db):
    client, db = test_db
    repo = IncidentRepository(db)

    now = datetime.now(timezone.utc)
    incident = Incident(
        id="INC-PM-API-01",
        title="API Service Spike",
        severity=IncidentSeverity.WARNING.value,
        state=IncidentState.RESOLVED.value,
        affected_service="api",
        source_alert="APIHighErrorRate",
        fingerprint="fp-pm-api",
        created_at=now - timedelta(seconds=60),
        resolved_at=now,
    )
    repo.create(incident)

    resp = client.get("/api/incidents/INC-PM-API-01/postmortem")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["incident_id"] == "INC-PM-API-01"
    assert "markdown_path" in data
    assert "json_path" in data
    assert os.path.isfile(data["markdown_path"])
    assert os.path.isfile(data["json_path"])
