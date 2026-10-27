"""
ResilienceLab — Runbook Catalogue & Selection Engine
Loads declarative YAML runbooks, validates safety preconditions, and compiles
executable remediation action sequences for active incidents.
"""

from dataclasses import dataclass, field
import logging
import os
from typing import Any, Dict, List, Optional
import yaml

from automation.incident.config import settings
from automation.incident.models.incident import Incident

logger = logging.getLogger("incident.runbooks")


@dataclass
class RunbookActionPlan:
    """Compiled remediation step with parameters and risk level."""
    step: int
    action_type: str
    target_resource: str
    risk_level: str
    requires_approval: bool
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunbookPlan:
    """Complete compiled runbook ready for execution."""
    runbook_id: str
    name: str
    description: str
    target_service: str
    actions: List[RunbookActionPlan] = field(default_factory=list)


class RunbookSelectionEngine:
    """Loads, validates, and matches declarative runbooks to incidents."""

    def __init__(self, runbooks_dir: str = "automation/runbooks"):
        self.runbooks_dir = runbooks_dir
        self.runbooks: Dict[str, Dict[str, Any]] = {}
        self.load_runbooks()

    def load_runbooks(self) -> None:
        """Scan and parse all YAML files in the runbooks directory."""
        if not os.path.isdir(self.runbooks_dir):
            logger.warning("Runbooks directory '%s' does not exist", self.runbooks_dir)
            return

        loaded = {}
        for fname in os.listdir(self.runbooks_dir):
            if fname.endswith(".yaml") or fname.endswith(".yml"):
                fpath = os.path.join(self.runbooks_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                        if isinstance(data, dict) and "id" in data:
                            self._validate_runbook_schema(data)
                            loaded[data["id"]] = data
                            logger.debug("Loaded runbook: %s (%s)", data["id"], fname)
                except Exception as exc:
                    logger.error("Failed to parse runbook %s: %s", fpath, exc)

        self.runbooks = loaded
        logger.info("Loaded %d declarative runbooks from %s", len(self.runbooks), self.runbooks_dir)

    def _validate_runbook_schema(self, data: Dict[str, Any]) -> None:
        """Ensure required fields and safe structure."""
        required = ["id", "name", "actions"]
        for req in required:
            if req not in data:
                raise ValueError(f"Runbook missing mandatory field '{req}'")

        for act in data.get("actions", []):
            if "action_type" not in act or "target_resource" not in act:
                raise ValueError(f"Runbook action missing action_type or target_resource: {act}")

    def select_runbook(
        self,
        incident: Incident,
        explicit_runbook_id: Optional[str] = None,
    ) -> Optional[RunbookPlan]:
        """
        Match an incident to the best matching runbook and compile concrete action plan.
        """
        # Strategy 1: Explicit runbook ID (e.g. from automated diagnosis)
        if explicit_runbook_id and explicit_runbook_id in self.runbooks:
            return self._compile_plan(self.runbooks[explicit_runbook_id], incident)

        service = incident.affected_service.lower()
        alertname = incident.source_alert

        # Strategy 2: Match by alertname and target service
        for rb_id, rb in self.runbooks.items():
            matching_alerts = rb.get("matching_alerts", [])
            target_svc = rb.get("target_service", "").lower()
            if alertname in matching_alerts and (not target_svc or target_svc == service):
                return self._compile_plan(rb, incident)

        # Strategy 3: Match by alertname alone
        for rb_id, rb in self.runbooks.items():
            matching_alerts = rb.get("matching_alerts", [])
            if alertname in matching_alerts:
                return self._compile_plan(rb, incident)

        # Strategy 4: Fallback to service default
        for rb_id, rb in self.runbooks.items():
            if rb.get("target_service", "").lower() == service:
                return self._compile_plan(rb, incident)

        logger.warning(
            "No matching runbook found for incident %s (service=%s, alert=%s)",
            incident.id,
            service,
            alertname,
        )
        return None

    def _compile_plan(self, rb: Dict[str, Any], incident: Incident) -> RunbookPlan:
        """Resolve placeholders and build executable action plan."""
        service = incident.affected_service.lower()
        namespace = incident.namespace or settings.ALLOWED_NAMESPACE

        compiled_actions: List[RunbookActionPlan] = []
        for act in rb.get("actions", []):
            # Substitute template variables
            raw_target = act["target_resource"]
            resolved_target = (
                raw_target.replace("{service}", service)
                .replace("{namespace}", namespace)
            )

            plan_action = RunbookActionPlan(
                step=act.get("step", len(compiled_actions) + 1),
                action_type=act["action_type"],
                target_resource=resolved_target,
                risk_level=act.get("risk_level", "LOW").upper(),
                requires_approval=act.get("requires_approval", False),
                description=act.get("description", f"Execute {act['action_type']} on {resolved_target}"),
                parameters=act.get("parameters", {}),
            )
            compiled_actions.append(plan_action)

        return RunbookPlan(
            runbook_id=rb["id"],
            name=rb.get("name", rb["id"]),
            description=rb.get("description", ""),
            target_service=rb.get("target_service", service),
            actions=compiled_actions,
        )
