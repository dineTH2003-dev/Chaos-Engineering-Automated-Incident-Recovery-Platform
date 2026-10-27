"""
ResilienceLab — Phase 5 Unit Tests: Automated Rollback and Incident Integration.
"""

import os
from unittest.mock import MagicMock, patch

from automation.deployment.rollback import RollbackOrchestrator


def test_rollback_lock_acquisition_and_release(tmp_path):
    """Tests that RollbackOrchestrator enforces mutual exclusion using a lock file."""
    orchestrator = RollbackOrchestrator()
    lock_file = "/tmp/resiliencelab_deployment.lock"

    if os.path.exists(lock_file):
        os.remove(lock_file)

    assert orchestrator._acquire_lock() is True
    assert os.path.exists(lock_file)

    # Second acquisition fails
    assert orchestrator._acquire_lock() is False

    orchestrator._release_lock()
    assert not os.path.exists(lock_file)


def test_rollback_notification_mocked():
    """Tests that rollback notifies the Phase 4 webhook receiver."""
    orchestrator = RollbackOrchestrator(incident_engine_url="http://mock-incident-engine:5001")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok", "incident_id": "INC-20261027-TEST"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        inc_id = orchestrator.notify_incident_engine(
            deployment_id="DEP-TEST-999",
            service="api",
            failed_tag="bad-v2",
            restored_tag="good-v1",
            failure_reason="Readiness probe failed",
        )
        assert inc_id == "INC-20261027-TEST"


def test_rollback_execution_flow_mocked():
    """Tests the full rollback orchestration execution flow."""
    orchestrator = RollbackOrchestrator()

    with patch.object(orchestrator, "_acquire_lock", return_value=True), \
         patch.object(orchestrator, "_release_lock"), \
         patch("automation.deployment.rollback.update_gitops_image_tag") as mock_update, \
         patch("automation.deployment.rollback.trigger_flux_reconciliation", return_value=True), \
         patch.object(orchestrator.verifier, "execute_verification") as mock_verif, \
         patch.object(orchestrator, "notify_incident_engine", return_value="INC-20261027-ROLLBACK"):

        mock_verif.return_value = MagicMock(passed=True)

        res = orchestrator.execute_rollback(
            service="api",
            failed_tag="v2-broken",
            deployment_id="DEP-12345",
            failure_reason="Elevated 5xx error rate",
            target_tag="v1-stable",
        )

        assert res.recovered is True
        assert res.restored_image_tag == "v1-stable"
        assert res.incident_id == "INC-20261027-ROLLBACK"
        mock_update.assert_called_once()
