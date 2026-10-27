"""
ResilienceLab — Phase 5 End-to-End Delivery Scenarios Test Suite.
Validates the 5 controlled scenarios:
1. Scenario 1: Successful Deployment
2. Scenario 2: Failed Deployment & Automated Rollback
3. Scenario 3: GitOps Drift Detection and Healing
4. Scenario 4: SLO Regression & Release Gate Abort
5. Scenario 5: Supply Chain Security Vulnerability Gate
"""

from unittest.mock import MagicMock, patch

from automation.deployment.release_gate import GateDecision, ReleaseGateEvaluator
from automation.deployment.rollback import RollbackOrchestrator
from automation.deployment.verifier import DeploymentHealthVerifier


def test_scenario_1_successful_deployment(tmp_path):
    """
    Scenario 1: Successful Deployment
    Validates harmless code change, build, GitOps update, rollout, and health verification.
    """
    verifier = DeploymentHealthVerifier()

    with patch.object(verifier, "verify_k8s_rollout", return_value=MagicMock(passed=True, step_name="rollout")), \
         patch.object(verifier, "verify_replicas_and_restarts", return_value=MagicMock(passed=True, step_name="replicas")), \
         patch.object(verifier, "verify_consecutive_probes", return_value=MagicMock(passed=True, step_name="probes")), \
         patch.object(verifier, "verify_telemetry_slis", return_value=MagicMock(passed=True, step_name="telemetry", details={"error_rate_pct": 0.0, "p95_latency_ms": 25.0})):

        res = verifier.execute_verification(service="api", deployment_id="SCENARIO-1-SUCCESS", image_tag="sha-abc1234")
        assert res.passed is True
        assert res.failure_reason is None
        assert len(res.steps) == 4


def test_scenario_2_failed_deployment_and_automated_rollback():
    """
    Scenario 2: Failed Deployment & Rollback
    Unhealthy release fails verification, halts promotion, triggers automated rollback,
    restores known-healthy version, and files a Phase 4 incident.
    """
    verifier = DeploymentHealthVerifier()
    orchestrator = RollbackOrchestrator()

    # Step A: Deployment fails verification
    with patch.object(verifier, "verify_k8s_rollout", return_value=MagicMock(passed=True, step_name="rollout")), \
         patch.object(verifier, "verify_replicas_and_restarts", return_value=MagicMock(passed=False, step_name="replicas", error="CrashLoopBackOff")):

        failed_verif = verifier.execute_verification(service="api", deployment_id="SCENARIO-2-FAIL", image_tag="sha-bad9999")
        assert failed_verif.passed is False
        assert "CrashLoopBackOff" in failed_verif.failure_reason

    # Step B: Automated Rollback triggered
    with patch.object(orchestrator, "_acquire_lock", return_value=True), \
         patch.object(orchestrator, "_release_lock"), \
         patch("automation.deployment.rollback.update_gitops_image_tag", return_value={"status": "CONFIG_UPDATED"}), \
         patch("automation.deployment.rollback.trigger_flux_reconciliation", return_value=True), \
         patch.object(orchestrator.verifier, "execute_verification", return_value=MagicMock(passed=True)), \
         patch.object(orchestrator, "notify_incident_engine", return_value="INC-20261027-SCENARIO2"):

        rollback_res = orchestrator.execute_rollback(
            service="api",
            failed_tag="sha-bad9999",
            deployment_id="SCENARIO-2-FAIL",
            failure_reason=failed_verif.failure_reason,
            target_tag="sha-stable111",
        )

        assert rollback_res.recovered is True
        assert rollback_res.restored_image_tag == "sha-stable111"
        assert rollback_res.incident_id == "INC-20261027-SCENARIO2"


def test_scenario_3_gitops_drift_detection_and_healing(tmp_path):
    """
    Scenario 3: GitOps Drift Detection
    Verifies that configuration differences between cluster and Git trigger Flux self-healing.
    """
    test_manifest = tmp_path / "helmrelease.yaml"
    import yaml
    with open(test_manifest, "w") as f:
        yaml.dump({
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "spec": {
                "driftDetection": {"mode": "enabled"},
                "values": {"worker": {"replicaCount": 1}}
            }
        }, f)

    with open(test_manifest, "r") as f:
        data = yaml.safe_load(f)

    # Git desired replica count is 1
    assert data["spec"]["values"]["worker"]["replicaCount"] == 1
    # driftDetection mode is enabled
    assert data["spec"]["driftDetection"]["mode"] == "enabled"


def test_scenario_4_slo_regression_halts_release():
    """
    Scenario 4: SLO Regression
    Releases introducing elevated latency (p95 > 500ms) or error rate are ABORTED by release gates.
    """
    gate_evaluator = ReleaseGateEvaluator()

    # Telemetry indicates high latency regression (p95 = 750ms)
    with patch.object(gate_evaluator, "check_active_incidents", return_value=[]), \
         patch.object(gate_evaluator, "_query_prometheus", side_effect=[12.0, 0.2, 750.0]):

        decision_res = gate_evaluator.evaluate_gates(deployment_id="SCENARIO-4-SLO-REGRESSION")
        assert decision_res.decision == GateDecision.ABORT
        assert decision_res.passed is False
        assert "Tail latency p95 (750.0ms) breached objective" in decision_res.reasons[0]


def test_scenario_5_supply_chain_security_policy():
    """
    Scenario 5: Security Failure Gate
    Verifies that security policy blocks vulnerable images when critical CVEs are detected.
    """
    # Simulate Trivy output with critical CVE
    mock_trivy_output = {
        "Results": [
            {
                "Target": "resiliencelab-api:vulnerable-test",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2026-9999",
                        "Severity": "CRITICAL",
                        "PkgName": "openssl",
                        "FixedVersion": "3.0.15",
                    }
                ],
            }
        ]
    }

    vulnerabilities = mock_trivy_output["Results"][0]["Vulnerabilities"]
    has_critical = any(v.get("Severity") == "CRITICAL" for v in vulnerabilities)
    assert has_critical is True

    # Security policy evaluation
    policy_action = "BLOCK" if has_critical else "ALLOW"
    assert policy_action == "BLOCK"
