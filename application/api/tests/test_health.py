"""Basic health, readiness, and metrics endpoint tests using TestClient."""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Create a test client fixture with mocked database lifecycle for isolated unit tests."""
    with (
        patch("app.main.startup_db", return_value=True),
        patch("app.main.shutdown_db", return_value=None),
    ):
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client


def test_root_returns_service_info(client: TestClient) -> None:
    """Verify that GET / returns service information and running status."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data == {
        "service": "resiliencelab-api",
        "status": "running",
        "version": "1.0.0",
    }


def test_health_returns_200(client: TestClient) -> None:
    """Verify that GET /health returns 200 and healthy status (liveness probe)."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_metrics_returns_prometheus_format(client: TestClient) -> None:
    """Verify that GET /metrics returns Prometheus text exposition format."""
    response = client.get("/metrics")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/plain" in content_type or "version=0.0.4" in content_type
    assert "http_requests_total" in response.text
    assert "application_errors_total" in response.text


def test_ready_returns_200_when_db_connected(client: TestClient) -> None:
    """Verify that GET /ready returns 200 when database connectivity check succeeds."""
    with patch("app.main.check_db_connection", return_value=True):
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "database": "connected",
        }


def test_ready_returns_503_when_db_disconnected(client: TestClient) -> None:
    """Verify that GET /ready returns 503 when database connectivity check fails."""
    with patch("app.main.check_db_connection", return_value=False):
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "database": "disconnected",
        }


@pytest.mark.skip(reason="Requires running PostgreSQL instance")
def test_list_orders_live_db(client: TestClient) -> None:
    """Live database test: list orders."""
    response = client.get("/api/orders")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.skip(reason="Requires running PostgreSQL instance")
def test_create_order_live_db(client: TestClient) -> None:
    """Live database test: create order."""
    response = client.post("/api/orders", json={"customer_name": "Test Customer"})
    assert response.status_code == 201
    data = response.json()
    assert data["customer_name"] == "Test Customer"
    assert data["status"] == "pending"
    assert "id" in data
