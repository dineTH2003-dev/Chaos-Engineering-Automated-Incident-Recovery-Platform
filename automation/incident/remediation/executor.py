"""
ResilienceLab — Remediation Executor
Executes verified and approved remediation operations against Kubernetes with dry-run support.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import subprocess
import time
from typing import Any, Dict, Optional

from automation.incident.config import settings
from automation.incident.models.remediation import (
    ApprovalDecision,
    ApprovalRequest,
    RemediationAction,
    RemediationRisk,
    RemediationState,
)
from automation.incident.remediation.policy import (
    PolicyViolationError,
    RemediationPolicyEngine,
)

logger = logging.getLogger("incident.remediation.executor")


@dataclass
class ExecutionResult:
    """Outcome of a remediation action execution."""
    success: bool
    action_type: str
    target_resource: str
    dry_run: bool
    output: str
    error: Optional[str] = None


class RemediationExecutor:
    """Executes safe Kubernetes remediations with strict guardrails and dry-run toggle."""

    def __init__(
        self,
        policy_engine: Optional[RemediationPolicyEngine] = None,
        default_dry_run: bool = settings.DRY_RUN,
    ):
        self.policy = policy_engine or RemediationPolicyEngine()
        self.default_dry_run = default_dry_run

    def execute(
        self,
        action: RemediationAction,
        approval: Optional[ApprovalRequest] = None,
        override_dry_run: Optional[bool] = None,
    ) -> ExecutionResult:
        """
        Execute a remediation action.
        Validates safety policies and approval requirements before execution.
        """
        dry_run = self.default_dry_run if override_dry_run is None else override_dry_run

        # 1. Validate safety policy
        self.policy.validate_action(
            action_type=action.action_type,
            target_resource=action.target_resource,
            namespace=action.namespace,
            parameters=action.parameters,
            risk_level=action.risk_level,
        )

        # 2. Check approval requirement
        if action.requires_approval:
            if not approval:
                raise PolicyViolationError(
                    f"Action '{action.action_type}' on '{action.target_resource}' requires human approval, but no approval request was provided."
                )
            if approval.decision != ApprovalDecision.APPROVED.value:
                raise PolicyViolationError(
                    f"Action '{action.action_type}' cannot execute because approval status is '{approval.decision}'."
                )

        # 3. Dry-Run Execution Branch
        if dry_run:
            cmd_preview = self._format_command(action)
            dry_run_output = (
                f"[DRY-RUN SIMULATION] Would execute: {cmd_preview} "
                f"(risk={action.risk_level}, parameters={action.parameters})"
            )
            logger.info(dry_run_output)
            return ExecutionResult(
                success=True,
                action_type=action.action_type,
                target_resource=action.target_resource,
                dry_run=True,
                output=dry_run_output,
            )

        # 4. Live Guarded Execution Branch
        logger.warning(
            "EXECUTING LIVE REMEDIATION: %s on %s in namespace %s (risk=%s)",
            action.action_type,
            action.target_resource,
            action.namespace,
            action.risk_level,
        )

        try:
            if action.action_type == "verify_self_healing":
                return self._exec_verify_self_healing(action)
            elif action.action_type == "restart_deployment":
                return self._exec_restart_deployment(action)
            elif action.action_type == "scale_deployment":
                return self._exec_scale_deployment(action)
            elif action.action_type == "verify_pvc_integrity":
                return self._exec_verify_pvc(action)
            elif action.action_type == "check_chaos_mesh":
                return self._exec_check_chaos(action)
            else:
                raise PolicyViolationError(f"Unsupported action type: {action.action_type}")
        except Exception as exc:
            logger.error("Remediation execution failed: %s", exc)
            return ExecutionResult(
                success=False,
                action_type=action.action_type,
                target_resource=action.target_resource,
                dry_run=False,
                output="",
                error=str(exc),
            )

    def _format_command(self, action: RemediationAction) -> str:
        """Format what the live kubectl command would look like."""
        act = action.action_type
        tgt = action.target_resource
        ns = action.namespace
        params = action.parameters or {}

        if act == "restart_deployment":
            return f"kubectl rollout restart {tgt} -n {ns}"
        elif act == "scale_deployment":
            replicas = params.get("replicas", 1)
            return f"kubectl scale {tgt} --replicas={replicas} -n {ns}"
        elif act == "verify_pvc_integrity":
            pvc = params.get("pvc_name", "orders-db-pvc")
            return f"kubectl get pvc {pvc} -n {ns} -o json"
        elif act == "verify_self_healing":
            wait = params.get("wait_seconds", 15)
            return f"verify_self_healing: wait {wait}s and check {tgt} status"
        elif act == "check_chaos_mesh":
            return f"kubectl get networkchaos,podchaos,stresschaos -n {ns}"
        return f"kubectl {act} {tgt} -n {ns}"

    def _exec_restart_deployment(self, action: RemediationAction) -> ExecutionResult:
        """Execute kubectl rollout restart."""
        # e.g., target_resource="deploy/api"
        cmd = ["kubectl", "rollout", "restart", action.target_resource, "-n", action.namespace]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        return ExecutionResult(
            success=True,
            action_type=action.action_type,
            target_resource=action.target_resource,
            dry_run=False,
            output=res.stdout.strip() or f"Restarted {action.target_resource}",
        )

    def _exec_scale_deployment(self, action: RemediationAction) -> ExecutionResult:
        """Execute kubectl scale deployment."""
        replicas = (action.parameters or {}).get("replicas", 1)
        cmd = [
            "kubectl", "scale", action.target_resource,
            f"--replicas={replicas}",
            "-n", action.namespace,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        return ExecutionResult(
            success=True,
            action_type=action.action_type,
            target_resource=action.target_resource,
            dry_run=False,
            output=res.stdout.strip() or f"Scaled {action.target_resource} to {replicas} replicas",
        )

    def _exec_verify_pvc(self, action: RemediationAction) -> ExecutionResult:
        """Verify PVC status is Bound."""
        pvc_name = (action.parameters or {}).get("pvc_name", "orders-db-pvc")
        cmd = ["kubectl", "get", "pvc", pvc_name, "-n", action.namespace, "-o", "json"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
        data = json.loads(res.stdout)
        phase = data.get("status", {}).get("phase", "Unknown")
        if phase != "Bound":
            raise RuntimeError(f"PVC {pvc_name} is in unexpected phase: {phase}")

        return ExecutionResult(
            success=True,
            action_type=action.action_type,
            target_resource=action.target_resource,
            dry_run=False,
            output=f"Verified PVC {pvc_name} is healthy and Bound.",
        )

    def _exec_verify_self_healing(self, action: RemediationAction) -> ExecutionResult:
        """Wait and inspect whether Kubernetes ReplicaSet recovers target."""
        wait_seconds = (action.parameters or {}).get("wait_seconds", 5)
        time.sleep(min(wait_seconds, 15))

        cmd = ["kubectl", "get", action.target_resource, "-n", action.namespace, "-o", "json"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
        data = json.loads(res.stdout)
        status = data.get("status", {})
        ready = status.get("readyReplicas", 0)
        desired = data.get("spec", {}).get("replicas", 0)

        output = f"Self-healing check for {action.target_resource}: {ready}/{desired} ready replicas."
        return ExecutionResult(
            success=(ready >= desired and desired > 0),
            action_type=action.action_type,
            target_resource=action.target_resource,
            dry_run=False,
            output=output,
        )

    def _exec_check_chaos(self, action: RemediationAction) -> ExecutionResult:
        """Inspect active Chaos experiments."""
        cmd = ["kubectl", "get", "networkchaos,podchaos,stresschaos", "-n", action.namespace, "-o", "json"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
        data = json.loads(res.stdout)
        items = data.get("items", [])
        return ExecutionResult(
            success=True,
            action_type=action.action_type,
            target_resource=action.target_resource,
            dry_run=False,
            output=f"Found {len(items)} Chaos Mesh CRDs in namespace {action.namespace}.",
        )
