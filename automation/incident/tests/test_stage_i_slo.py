"""
Unit tests for Stage I: SRE SLO & Error-Budget Intelligence Engine.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
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
from automation.incident.slo.engine import (
    ALLOWED_DOWNTIME_SECONDS,
    BURN_RATE_FAST_THRESHOLD,
    BURN_RATE_SLOW_THRESHOLD,
    SLO_AVAILABILITY_TARGET_PCT,
    SLOEngine,
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


def test_slo_burn_rate_calculations():
    engine = SLOEngine()

    # Case 1: Healthy baseline (0% errors)
    with patch.object(engine, "_query_error_rate_pct", return_value=0.0):
        summary = engine.get_slo_summary()
        assert summary.current_burn_rate == 0.0
        assert summary.fast_burn_active is False
        assert summary.slow_burn_active is False
        assert summary.current_availability_sli_pct == 100.0

    # Case 2: Slow burn (4% error rate -> 4 / 0.5 = 8.0x)
    with patch.object(engine, "_query_error_rate_pct", return_value=4.0):
        summary_slow = engine.get_slo_summary()
        assert summary_slow.current_burn_rate == 8.0
        assert summary_slow.slow_burn_active is True
        assert summary_slow.fast_burn_active is False

    # Case 3: Fast burn (10% error rate -> 10 / 0.5 = 20.0x > 14.4x)
    with patch.object(engine, "_query_error_rate_pct", return_value=10.0):
        summary_fast = engine.get_slo_summary()
        assert summary_fast.current_burn_rate == 20.0
        assert summary_fast.fast_burn_active is True


def test_incident_budget_consumption():
    engine = SLOEngine()
    now = datetime.now(timezone.utc)

    # Incident lasting 129.6 seconds with critical severity (100% outage)
    # 129.6 / 12960 = exactly 1.0% of total 30-day budget!
    incident = Incident(
        id="INC-SLO-001",
        title="Database Down",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.RESOLVED.value,
        affected_service="postgres",
        source_alert="DatabaseConnectionFailures",
        created_at=now - timedelta(seconds=129.6),
        resolved_at=now,
    )

    impact = engine.compute_incident_budget_consumption(incident)
    assert impact["incident_id"] == "INC-SLO-001"
    assert impact["budget_consumed_pct"] == 1.0

    # Summary with this incident reduces remaining budget from 100% to 99.0%
    with patch.object(engine, "_query_error_rate_pct", return_value=0.0):
        summary = engine.get_slo_summary(incidents=[incident])
        assert summary.error_budget_remaining_pct == 99.0
        assert summary.consumed_downtime_seconds == 129.6


def test_slo_api_endpoints(test_db):
    client, db = test_db
    repo = IncidentRepository(db)

    now = datetime.now(timezone.utc)
    incident = Incident(
        id="INC-SLO-API-01",
        title="API Outage",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.RESOLVED.value,
        affected_service="api",
        source_alert="APIHighErrorRate",
        fingerprint="fp-slo-api",
        created_at=now - timedelta(seconds=64.8),
        resolved_at=now,
    )
    repo.create(incident)

    # Test GET /api/slo
    resp = client.get("/api/slo")
    assert resp.status_code == 200
    data = resp.json()
    assert "availability_target_pct" in data
    assert "current_availability_sli_pct" in data
    assert "error_budget_remaining_pct" in data
    assert data["total_incidents_recorded"] >= 1

    # Test GET /api/slo/incidents/{id}
    impact_resp = client.get("/api/slo/incidents/INC-SLO-API-01")
    assert impact_resp.status_code == 200
    impact_data = impact_resp.json()
    assert impact_data["incident_id"] == "INC-SLO-API-01"
    assert impact_data["budget_consumed_pct"] == 0.5
