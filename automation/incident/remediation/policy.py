"""
ResilienceLab — Remediation Policy Engine
Enforces strict allowlists, namespace restrictions, resource bounds, cooldowns, and approval gates.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional, Set

from automation.incident.config import settings
from automation.incident.models.remediation import RemediationRisk

logger = logging.getLogger("incident.remediation.policy")

# Strict allowlist of permissible remediation actions
PERMISSIBLE_ACTIONS: Set[str] = {
    "verify_self_healing",
    "restart_deployment",
    "scale_deployment",
    "verify_pvc_integrity",
    "check_chaos_mesh",
}

# Bounds for deployment scaling
MIN_REPLICAS = 1
MAX_REPLICAS = 4


class PolicyViolationError(ValueError):
    """Raised when a remediation violates SRE safety policies."""
    pass


class RemediationPolicyEngine:
    """Validates remediation actions against platform safety policies."""

    def __init__(
        self,
        allowed_namespace: str = settings.ALLOWED_NAMESPACE,
        forbidden_namespaces: Optional[Set[str]] = None,
        max_retries: int = settings.MAX_REMEDIATION_RETRIES,
        cooldown_seconds: int = settings.REMEDIATION_COOLDOWN_SECONDS,
    ):
        self.allowed_namespace = allowed_namespace
        self.forbidden_namespaces = forbidden_namespaces or settings.FORBIDDEN_NAMESPACES
        self.max_retries = max_retries
        self.cooldown_seconds = cooldown_seconds

    def validate_action(
        self,
        action_type: str,
        target_resource: str,
        namespace: str,
        parameters: Optional[Dict[str, Any]] = None,
        risk_level: str = RemediationRisk.LOW.value,
        prior_execution_count: int = 0,
        last_executed_at: Optional[datetime] = None,
    ) -> bool:
        """
        Validate proposed remediation against safety policies.
        Raises PolicyViolationError on any infraction.
        """
        # Guardrail 1: Action Allowlist
        if action_type not in PERMISSIBLE_ACTIONS:
            raise PolicyViolationError(
                f"Action '{action_type}' is not in the permissible remediation allowlist: {sorted(PERMISSIBLE_ACTIONS)}"
            )

        # Guardrail 2: Namespace Whitelist
        if namespace != self.allowed_namespace or namespace in self.forbidden_namespaces:
            raise PolicyViolationError(
                f"Target namespace '{namespace}' is not permitted. Only '{self.allowed_namespace}' is allowed."
            )

        # Guardrail 3: Data Safety (No PVC deletion or destructive operations)
        lower_target = target_resource.lower()
        if "delete" in lower_target or "drop" in lower_target or "truncate" in lower_target:
            raise PolicyViolationError(
                f"Destructive operation detected in target '{target_resource}'. Forbidden by data safety policy."
            )

        # Guardrail 4: Resource Bounds (Replica count bounds)
        if action_type == "scale_deployment":
            params = parameters or {}
            replicas = params.get("replicas")
            if replicas is None or not isinstance(replicas, int):
                raise PolicyViolationError("scale_deployment action requires an integer 'replicas' parameter")
            if replicas < MIN_REPLICAS or replicas > MAX_REPLICAS:
                raise PolicyViolationError(
                    f"Replica count {replicas} violates bounds ({MIN_REPLICAS} <= replicas <= {MAX_REPLICAS})"
                )

        # Guardrail 5: Retry limits
        if prior_execution_count >= self.max_retries:
            raise PolicyViolationError(
                f"Remediation retry limit reached ({prior_execution_count}/{self.max_retries} attempts)"
            )

        # Guardrail 6: Cooldown window
        if last_executed_at and action_type in {"restart_deployment", "scale_deployment"}:
            now = datetime.now(timezone.utc)
            elapsed = (now - last_executed_at).total_seconds()
            if elapsed < self.cooldown_seconds:
                raise PolicyViolationError(
                    f"Action '{action_type}' on '{target_resource}' is in cooldown ({int(elapsed)}s < {self.cooldown_seconds}s)"
                )

        return True

    def requires_human_approval(self, risk_level: str, action_type: str) -> bool:
        """Determine if an action requires human approval before execution."""
        risk = risk_level.upper()
        if risk == RemediationRisk.HIGH.value:
            return True
        if risk == RemediationRisk.MEDIUM.value and not settings.AUTO_APPROVE_LOW_RISK:
            return True
        return False
