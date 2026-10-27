"""
ResilienceLab — Phase 5 Unit & Mocked Integration Tests: Verifier & Release Gates.
"""

from unittest.mock import MagicMock, patch

from automation.deployment.release_gate import GateDecision, ReleaseGateEvaluator
from automation.deployment.verifier import DeploymentHealthVerifier


def test_verifier_k8s_rollout_mocked():
    """Tests verifier behavior when kubectl rollout status succeeds or fails."""
    verifier = DeploymentHealthVerifier()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="deployment successfully rolled out")
        res = verifier.verify_k8s_rollout("api")
        assert res.passed is True
        assert res.step_name == "k8s_rollout_status"

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = Exception("error: deployment timed out")
        res = verifier.verify_k8s_rollout("api")
        assert res.passed is False
        assert "timed out" in res.error


def test_verifier_consecutive_probes_mocked():
    """Tests that consecutive probes require multiple continuous successes."""
    verifier = DeploymentHealthVerifier(consecutive_success_required=3, probe_interval_seconds=0.01)

    # All probes succeed
    with patch.object(verifier, "_probe_http", return_value=True):
        res = verifier.verify_consecutive_probes("api")
        assert res.passed is True
        assert res.details["achieved_streak"] == 3

    # Probes fail intermittently
    with patch.object(verifier, "_probe_http", side_effect=[True, False] * 20):
        res = verifier.verify_consecutive_probes("api")
        assert res.passed is False


def test_verifier_telemetry_slis_mocked():
    """Tests error rate and latency evaluation against thresholds."""
    verifier = DeploymentHealthVerifier(max_error_rate_pct=1.0, max_p95_latency_ms=500.0)

    # Healthy metrics
    with patch.object(verifier, "query_prometheus_metric", side_effect=[0.2, 120.0]):
        res = verifier.verify_telemetry_slis()
        assert res.passed is True
        assert res.details["error_rate_pct"] == 0.2

    # High error rate breaches gate
    with patch.object(verifier, "query_prometheus_metric", side_effect=[5.5, 80.0]):
        res = verifier.verify_telemetry_slis()
        assert res.passed is False
        assert "5xx rate 5.50%" in res.error


def test_release_gate_evaluator_promote_decision():
    """Tests that evaluator returns PROMOTE when all reliability gates pass."""
    evaluator = ReleaseGateEvaluator()

    with patch.object(evaluator, "check_active_incidents", return_value=[]), \
         patch.object(evaluator, "_query_prometheus", side_effect=[10.0, 0.1, 45.0]):
        # RPS: 10.0, Error rate: 0.1%, p95 latency: 45ms
        res = evaluator.evaluate_gates(deployment_id="DEP-TEST-001")
        assert res.decision == GateDecision.PROMOTE
        assert res.passed is True
        assert res.metrics["availability_pct"] == 99.9


def test_release_gate_evaluator_aborts_on_active_incident():
    """Tests that active critical incidents immediately abort release promotion."""
    evaluator = ReleaseGateEvaluator()

    with patch.object(evaluator, "check_active_incidents", return_value=["INC-20261027-01 (API Outage)"]):
        res = evaluator.evaluate_gates(deployment_id="DEP-TEST-002")
        assert res.decision == GateDecision.ABORT
        assert res.passed is False
        assert "active critical incident" in res.reasons[0]


def test_release_gate_evaluator_aborts_on_fast_burn():
    """Tests that high error burn rates trigger ABORT decision."""
    evaluator = ReleaseGateEvaluator()

    with patch.object(evaluator, "check_active_incidents", return_value=[]), \
         patch.object(evaluator, "_query_prometheus", side_effect=[15.0, 8.0, 600.0]):
        # Error rate 8% results in fast burn >> 14.4x
        res = evaluator.evaluate_gates(deployment_id="DEP-TEST-003")
        assert res.decision == GateDecision.ABORT
        assert res.passed is False
