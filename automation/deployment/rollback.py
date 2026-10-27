#!/usr/bin/env python3
"""
ResilienceLab — Automated GitOps-Compatible Rollback & Incident Escalation Engine
Reverts GitOps desired state to the previous known-healthy revision, triggers Flux reconciliation,
independently verifies recovery, and notifies the Phase 4 Incident Response Platform.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from typing import Optional
import urllib.request

from automation.deployment.gitops_updater import (
    DEFAULT_HELMRELEASE,
    DEPLOYMENT_LOG_FILE,
    trigger_flux_reconciliation,
    update_gitops_image_tag,
)
from automation.deployment.verifier import DeploymentHealthVerifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("deployment.rollback")

LOCK_FILE = "/tmp/resiliencelab_deployment.lock"
ROLLBACK_COOLDOWN_SECONDS = 30


@dataclass
class RollbackExecutionResult:
    rollback_id: str
    deployment_id: str
    service: str
    failed_image_tag: str
    restored_image_tag: str
    status: str
    recovered: bool
    incident_id: Optional[str]
    timeline: list


class RollbackOrchestrator:
    def __init__(
        self,
        namespace: str = "resiliencelab",
        incident_engine_url: str = "http://localhost:5001",
        manifest_path: str = DEFAULT_HELMRELEASE,
    ):
        self.namespace = namespace
        self.incident_engine_url = incident_engine_url.rstrip("/")
        self.manifest_path = manifest_path
        self.verifier = DeploymentHealthVerifier(namespace=namespace)

    def _acquire_lock(self) -> bool:
        """Acquires deployment lock to prevent concurrent deploy/rollback loops."""
        if os.path.exists(LOCK_FILE):
            try:
                mtime = os.path.getmtime(LOCK_FILE)
                if (time.time() - mtime) > 180:  # Lock expired after 3m
                    os.remove(LOCK_FILE)
                else:
                    return False
            except OSError:
                pass
        try:
            with open(LOCK_FILE, "w") as f:
                f.write(f"locked by rollback at {datetime.now(timezone.utc).isoformat()}")
            return True
        except OSError:
            return False

    def _release_lock(self):
        """Releases the deployment lock file."""
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
            except OSError:
                pass

    def get_last_known_healthy_tag(self, service: str) -> str:
        """Finds the most recent successful image tag for the service from audit log."""
        if not os.path.exists(DEPLOYMENT_LOG_FILE):
            return "c49d3e3"  # Baseline Phase 4 tag

        healthy_tag = "c49d3e3"
        try:
            with open(DEPLOYMENT_LOG_FILE, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if rec.get("service") in (service, "all") and rec.get("status") == "VERIFIED_HEALTHY":
                        healthy_tag = rec.get("new_tag", healthy_tag)
        except Exception as e:
            logger.warning(f"Could not parse deployment log: {e}")

        return healthy_tag

    def notify_incident_engine(
        self,
        deployment_id: str,
        service: str,
        failed_tag: str,
        restored_tag: str,
        failure_reason: str,
    ) -> Optional[str]:
        """Dispatches alert webhook to Phase 4 Incident Response Engine."""
        payload = {
            "version": "4",
            "groupKey": f"deployment-failure-{service}",
            "status": "firing",
            "receiver": "webhook",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "DeploymentHealthVerificationFailed",
                        "service": service,
                        "severity": "critical",
                        "namespace": self.namespace,
                    },
                    "annotations": {
                        "summary": f"Automated rollback triggered for {service} deployment {deployment_id}",
                        "description": f"Image {failed_tag} failed health verification: {failure_reason}. Safe GitOps rollback to {restored_tag} initiated.",
                        "deployment_id": deployment_id,
                        "failed_image": failed_tag,
                        "target_image": restored_tag,
                    },
                    "startsAt": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }

        try:
            url = f"{self.incident_engine_url}/alerts"
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                resp_data = json.loads(resp.read().decode())
                incident_id = resp_data.get("incident_id")
                logger.info(f"Phase 4 incident recorded: {incident_id}")
                return incident_id
        except Exception as e:
            logger.warning(f"Could not post alert to Incident Engine at {self.incident_engine_url}: {e}")
            return None

    def execute_rollback(
        self,
        service: str,
        failed_tag: str,
        deployment_id: str,
        failure_reason: str,
        target_tag: Optional[str] = None,
    ) -> RollbackExecutionResult:
        """
        Executes a controlled, GitOps-safe rollback:
        1. Acquires lock.
        2. Identifies target healthy tag.
        3. Updates GitOps manifest.
        4. Triggers Flux reconciliation.
        5. Independently verifies recovery.
        6. Notifies Phase 4 incident platform.
        """
        rollback_id = f"ROLLBACK-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        timeline = []
        timeline.append({"time": datetime.now(timezone.utc).isoformat(), "event": "ROLLBACK_INITIATED", "failed_tag": failed_tag})

        if not self._acquire_lock():
            raise RuntimeError("Another deployment or rollback operation is currently in progress. Aborting.")

        try:
            if not target_tag:
                target_tag = self.get_last_known_healthy_tag(service)

            logger.info(f"Reverting {service} desired state in GitOps to tag '{target_tag}'...")
            update_gitops_image_tag(
                service=service,
                new_tag=target_tag,
                manifest_path=self.manifest_path,
                actor="automated-rollback-controller",
            )
            timeline.append({"time": datetime.now(timezone.utc).isoformat(), "event": "GITOPS_REVERTED", "restored_tag": target_tag})

            # Force immediate Flux sync
            logger.info("Triggering Flux reconciliation...")
            trigger_flux_reconciliation(namespace=self.namespace)
            timeline.append({"time": datetime.now(timezone.utc).isoformat(), "event": "FLUX_RECONCILE_TRIGGERED"})

            # Wait for rollout and verify recovery
            logger.info("Verifying cluster steady-state recovery after rollback...")
            recovery_res = self.verifier.execute_verification(service=service, deployment_id=rollback_id, image_tag=target_tag)
            timeline.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "RECOVERY_VERIFIED" if recovery_res.passed else "RECOVERY_VERIFICATION_FAILED",
                "passed": recovery_res.passed,
            })

            # Notify Phase 4 Incident Engine
            incident_id = self.notify_incident_engine(
                deployment_id=deployment_id,
                service=service,
                failed_tag=failed_tag,
                restored_tag=target_tag,
                failure_reason=failure_reason,
            )
            if incident_id:
                timeline.append({"time": datetime.now(timezone.utc).isoformat(), "event": "INCIDENT_ESCALATED", "incident_id": incident_id})

            result = RollbackExecutionResult(
                rollback_id=rollback_id,
                deployment_id=deployment_id,
                service=service,
                failed_image_tag=failed_tag,
                restored_image_tag=target_tag,
                status="COMPLETED_RECOVERED" if recovery_res.passed else "COMPLETED_UNHEALTHY",
                recovered=recovery_res.passed,
                incident_id=incident_id,
                timeline=timeline,
            )

            # Persist rollback record
            os.makedirs("reports/deployments", exist_ok=True)
            with open(f"reports/deployments/{rollback_id}.json", "w") as f:
                json.dump(asdict(result), f, indent=2)

            return result

        finally:
            self._release_lock()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ResilienceLab Automated Rollback Engine")
    parser.add_argument("--service", default="api", help="Service name")
    parser.add_argument("--deployment-id", required=True, help="Failed deployment ID")
    parser.add_argument("--failed-tag", required=True, help="Failed image tag")
    parser.add_argument("--target-tag", help="Explicit tag to revert to (defaults to last healthy)")
    parser.add_argument("--reason", default="Post-rollout verification checks failed", help="Failure reason")

    args = parser.parse_args()

    orchestrator = RollbackOrchestrator()
    res = orchestrator.execute_rollback(
        service=args.service,
        failed_tag=args.failed_tag,
        deployment_id=args.deployment_id,
        failure_reason=args.reason,
        target_tag=args.target_tag,
    )

    if res.recovered:
        print(f"\n✅ ROLLBACK SUCCESSFUL: Restored {res.service} to {res.restored_image_tag} (Incident: {res.incident_id})")
        sys.exit(0)
    else:
        print(f"\n❌ ROLLBACK FAILED TO RECOVER SERVICE: {res.status}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
