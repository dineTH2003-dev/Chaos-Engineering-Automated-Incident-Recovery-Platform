"""
Unit tests for Stage E: Runbook Catalogue & Selection Engine.
"""

import pytest

from automation.incident.models.incident import Incident, IncidentSeverity, IncidentState
from automation.incident.runbooks.engine import RunbookSelectionEngine


@pytest.fixture
def runbook_engine():
    return RunbookSelectionEngine(runbooks_dir="automation/runbooks")


def test_load_all_runbooks(runbook_engine):
    assert len(runbook_engine.runbooks) >= 6
    expected_ids = {
        "api-unavailable",
        "database-unavailable",
        "worker-failure",
        "high-cpu",
        "high-latency",
        "pod-crash-loop",
    }
    assert expected_ids.issubset(set(runbook_engine.runbooks.keys()))


def test_select_api_unavailable_runbook(runbook_engine):
    incident = Incident(
        id="INC-TEST-API",
        title="API Error Rate High",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.INVESTIGATING.value,
        affected_service="api",
        source_alert="APIHighErrorRate",
    )
    plan = runbook_engine.select_runbook(incident)
    assert plan is not None
    assert plan.runbook_id == "api-unavailable"
    assert len(plan.actions) == 3

    # Check actions ordering and types
    assert plan.actions[0].action_type == "verify_self_healing"
    assert plan.actions[1].action_type == "restart_deployment"
    assert plan.actions[2].action_type == "scale_deployment"
    assert plan.actions[0].requires_approval is False


def test_select_database_unavailable_requires_approval(runbook_engine):
    incident = Incident(
        id="INC-TEST-DB",
        title="Database Down",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.INVESTIGATING.value,
        affected_service="postgres",
        source_alert="DatabaseConnectionFailures",
    )
    plan = runbook_engine.select_runbook(incident)
    assert plan is not None
    assert plan.runbook_id == "database-unavailable"
    assert len(plan.actions) == 2

    # Step 1: verify_pvc_integrity (low risk, no approval)
    assert plan.actions[0].action_type == "verify_pvc_integrity"
    assert plan.actions[0].requires_approval is False

    # Step 2: scale_deployment (high risk, REQUIRES APPROVAL)
    assert plan.actions[1].action_type == "scale_deployment"
    assert plan.actions[1].risk_level == "HIGH"
    assert plan.actions[1].requires_approval is True
    assert plan.actions[1].parameters.get("replicas") == 1


def test_select_worker_failure(runbook_engine):
    incident = Incident(
        id="INC-TEST-WORKER",
        title="Worker Failure",
        severity=IncidentSeverity.WARNING.value,
        state=IncidentState.INVESTIGATING.value,
        affected_service="worker",
        source_alert="WorkerProcessingFailures",
    )
    plan = runbook_engine.select_runbook(incident)
    assert plan is not None
    assert plan.runbook_id == "worker-failure"
    assert plan.actions[0].action_type == "restart_deployment"
    assert plan.actions[0].target_resource == "deploy/worker"


def test_select_pod_crash_loop_placeholder_substitution(runbook_engine):
    incident = Incident(
        id="INC-TEST-CRASH",
        title="Worker crashloop",
        severity=IncidentSeverity.CRITICAL.value,
        state=IncidentState.INVESTIGATING.value,
        affected_service="worker",
        source_alert="PodCrashLooping",
    )
    plan = runbook_engine.select_runbook(incident)
    assert plan is not None
    assert plan.runbook_id == "pod-crash-loop"
    assert plan.actions[0].target_resource == "deploy/worker"
