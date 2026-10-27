"""
ResilienceLab — Alert & Incident Correlation Engine
Correlates multi-service alert streams using topology dependency graph, temporal proximity,
and causal failure propagation rules.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Set

from automation.incident.models.incident import Incident, IncidentState

logger = logging.getLogger("incident.correlation")

# Topology Dependency Graph (Key depends on Values)
# e.g., "api" depends on ["postgres"], "worker" depends on ["postgres"]
SERVICE_DEPENDENCIES: Dict[str, List[str]] = {
    "api": ["postgres"],
    "worker": ["postgres"],
    "postgres": [],
}

# Alert causal signatures: which alerts represent root failures
ROOT_ALERT_SIGNATURES: Set[str] = {
    "DatabaseConnectionFailures",
    "PostgresDown",
    "PodCrashLooping",
}


@dataclass
class CorrelationResult:
    """Outcome of incident correlation analysis."""
    is_root: bool
    root_incident_id: Optional[str] = None
    correlated_incident_ids: List[str] = field(default_factory=list)
    relationship: str = "INDEPENDENT"
    explanation: str = "No correlated dependency detected"


class AlertCorrelationEngine:
    """Evaluates causal relationships between concurrent alerts and incidents."""

    def __init__(self, time_window_seconds: int = 180):
        self.time_window_seconds = time_window_seconds

    def correlate(
        self,
        candidate: Incident,
        active_incidents: List[Incident],
    ) -> CorrelationResult:
        """
        Evaluate candidate incident against currently active incidents to determine
        if candidate is a root cause, a downstream symptom, or co-occurring.
        """
        # Filter out candidate itself
        others = [inc for inc in active_incidents if inc.id != candidate.id]
        if not others:
            return CorrelationResult(
                is_root=True,
                root_incident_id=candidate.id,
                relationship="STANDALONE_ROOT",
                explanation=f"No other active incidents; {candidate.source_alert} on {candidate.affected_service} is primary root",
            )

        candidate_service = candidate.affected_service.lower()
        candidate_time = candidate.created_at or datetime.now(timezone.utc)
        dependencies = SERVICE_DEPENDENCIES.get(candidate_service, [])

        # Check 1: Is candidate a downstream dependent of an active upstream root incident?
        for other in others:
            other_service = other.affected_service.lower()
            if other_service in dependencies:
                # Upstream dependency is having an active incident!
                other_time = other.created_at or datetime.now(timezone.utc)
                delta = abs((candidate_time - other_time).total_seconds())

                if delta <= self.time_window_seconds:
                    logger.info(
                        "Incident %s (%s on %s) correlated as DOWNSTREAM_SYMPTOM of %s (%s on %s)",
                        candidate.id,
                        candidate.source_alert,
                        candidate_service,
                        other.id,
                        other.source_alert,
                        other_service,
                    )
                    return CorrelationResult(
                        is_root=False,
                        root_incident_id=other.id,
                        correlated_incident_ids=[other.id],
                        relationship="DOWNSTREAM_SYMPTOM",
                        explanation=(
                            f"Service '{candidate_service}' depends on '{other_service}'. "
                            f"Upstream incident {other.id} ({other.source_alert}) triggered within {int(delta)}s."
                        ),
                    )

        # Check 2: Is candidate the ROOT cause of other active downstream incidents?
        downstream_symptoms: List[str] = []
        for other in others:
            other_service = other.affected_service.lower()
            other_dependencies = SERVICE_DEPENDENCIES.get(other_service, [])
            if candidate_service in other_dependencies:
                other_time = other.created_at or datetime.now(timezone.utc)
                delta = abs((candidate_time - other_time).total_seconds())
                if delta <= self.time_window_seconds:
                    downstream_symptoms.append(other.id)

        if downstream_symptoms:
            logger.info(
                "Incident %s (%s on %s) identified as ROOT_CAUSE for downstream incidents: %s",
                candidate.id,
                candidate.source_alert,
                candidate_service,
                downstream_symptoms,
            )
            return CorrelationResult(
                is_root=True,
                root_incident_id=candidate.id,
                correlated_incident_ids=downstream_symptoms,
                relationship="ROOT_CAUSE",
                explanation=(
                    f"Incident {candidate.id} on '{candidate_service}' is root cause for "
                    f"{len(downstream_symptoms)} downstream incidents on dependent services."
                ),
            )

        # Check 3: Same service co-occurring alerts (e.g. HighCPU + HighLatency)
        co_occurring: List[str] = []
        for other in others:
            if other.affected_service.lower() == candidate_service:
                other_time = other.created_at or datetime.now(timezone.utc)
                delta = abs((candidate_time - other_time).total_seconds())
                if delta <= self.time_window_seconds:
                    co_occurring.append(other.id)

        if co_occurring:
            return CorrelationResult(
                is_root=True,
                root_incident_id=candidate.id,
                correlated_incident_ids=co_occurring,
                relationship="CO_OCCURRING",
                explanation=f"Co-occurring alert on same service '{candidate_service}' within {self.time_window_seconds}s.",
            )

        return CorrelationResult(
            is_root=True,
            root_incident_id=candidate.id,
            relationship="STANDALONE_ROOT",
            explanation=f"No causal linkage found with other active incidents.",
        )
