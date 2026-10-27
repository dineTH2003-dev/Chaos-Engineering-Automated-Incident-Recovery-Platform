"""
ResilienceLab — Alert Ingestion & Normalization Service
Parses Alertmanager payloads, extracts fingerprints, deduplicates firing alerts,
and persists incidents and alerts to PostgreSQL while maintaining backwards compatibility.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from automation.incident.config import settings
from automation.incident.models.incident import (
    Incident,
    IncidentAlert,
    IncidentSeverity,
    IncidentState,
)
from automation.incident.repositories.incident_repo import IncidentRepository
from automation.incident.repositories.timeline_repo import TimelineRepository

logger = logging.getLogger("incident.ingestion")

# In-memory buffer for backward compatibility with Phase 2/3 test scripts
_in_memory_alerts: List[Dict[str, Any]] = []


def get_raw_alerts_buffer() -> List[Dict[str, Any]]:
    """Return raw alerts buffer for backward compatibility."""
    return _in_memory_alerts


def clear_raw_alerts_buffer() -> None:
    """Clear raw alerts buffer for backward compatibility."""
    _in_memory_alerts.clear()


def append_legacy_log(record: Dict[str, Any]) -> None:
    """Append structured JSON record to reports/incidents/incidents.log."""
    try:
        log_dir = settings.INCIDENTS_LOG_DIR
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "incidents.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning("Could not write to legacy incident log: %s", exc)


def generate_incident_id() -> str:
    """Generate human-readable incident identifier: INC-YYYYMMDD-XXXX."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    random_part = hashlib.sha256(os.urandom(16)).hexdigest()[:4].upper()
    return f"INC-{date_part}-{random_part}"


def compute_fingerprint(labels: Dict[str, str]) -> str:
    """Deterministic fingerprint from sorted label key-value pairs."""
    sorted_pairs = sorted(f"{k}={v}" for k, v in labels.items())
    raw = ",".join(sorted_pairs)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_timestamp(ts_str: Optional[str]) -> datetime:
    """Safely parse ISO8601 timestamp or default to current UTC time."""
    if not ts_str or ts_str.startswith("0001-01-01"):
        return datetime.now(timezone.utc)
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return datetime.now(timezone.utc)


class AlertIngestionService:
    """Processes incoming alerts from Alertmanager."""

    def __init__(self, db: Session):
        self.db = db
        self.incident_repo = IncidentRepository(db)
        self.timeline_repo = TimelineRepository(db)

    def process_webhook_payload(self, payload: Dict[str, Any]) -> Tuple[List[str], int]:
        """
        Ingest an entire Alertmanager webhook payload.
        Returns (list_of_incident_ids_affected, count_of_alerts_processed).
        """
        status_state = payload.get("status", "firing")
        raw_alerts = payload.get("alerts", [])
        logger.info(
            "Processing Alertmanager payload status='%s', alert_count=%d",
            status_state,
            len(raw_alerts),
        )

        affected_incident_ids: List[str] = []
        processed_count = 0

        for raw in raw_alerts:
            incident = self.ingest_single_alert(raw, fallback_status=status_state)
            if incident and incident.id not in affected_incident_ids:
                affected_incident_ids.append(incident.id)
            processed_count += 1

        return affected_incident_ids, processed_count

    def ingest_single_alert(
        self,
        alert_data: Dict[str, Any],
        fallback_status: str = "firing",
    ) -> Optional[Incident]:
        """Normalize, deduplicate, and persist a single alert."""
        labels = alert_data.get("labels", {})
        annotations = alert_data.get("annotations", {})
        alert_status = alert_data.get("status", fallback_status).lower()

        alertname = labels.get("alertname", "UnknownAlert")
        severity = labels.get("severity", IncidentSeverity.WARNING.value).lower()
        if severity not in {s.value for s in IncidentSeverity}:
            severity = IncidentSeverity.WARNING.value

        service = (
            labels.get("service")
            or labels.get("app")
            or labels.get("job")
            or "resiliencelab"
        )
        namespace = labels.get("namespace", settings.ALLOWED_NAMESPACE)
        fingerprint = alert_data.get("fingerprint") or compute_fingerprint(labels)

        starts_at = parse_timestamp(alert_data.get("startsAt"))
        ends_at = parse_timestamp(alert_data.get("endsAt")) if alert_data.get("endsAt") else None
        generator_url = alert_data.get("generatorURL")

        summary = (
            annotations.get("summary")
            or annotations.get("message")
            or f"{alertname} triggered on {service}"
        )
        description = annotations.get("description") or summary

        # Legacy recording for Phase 2/3 compatibility
        legacy_record = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "status": alert_status,
            "alertname": alertname,
            "severity": severity,
            "namespace": namespace,
            "service": service,
            "pod": labels.get("pod", "unknown"),
            "summary": summary,
            "description": description,
            "startsAt": alert_data.get("startsAt", ""),
            "endsAt": alert_data.get("endsAt", ""),
            "fingerprint": fingerprint,
            "raw_labels": labels,
        }
        _in_memory_alerts.append(legacy_record)
        append_legacy_log(legacy_record)

        if alert_status == "firing":
            return self._handle_firing_alert(
                fingerprint=fingerprint,
                alertname=alertname,
                severity=severity,
                service=service,
                namespace=namespace,
                summary=summary,
                description=description,
                starts_at=starts_at,
                labels=labels,
                annotations=annotations,
                generator_url=generator_url,
            )
        elif alert_status == "resolved":
            return self._handle_resolved_alert(
                fingerprint=fingerprint,
                alertname=alertname,
                ends_at=ends_at,
                labels=labels,
                annotations=annotations,
            )
        else:
            logger.warning("Unrecognized alert status: %s", alert_status)
            return None

    def _handle_firing_alert(
        self,
        fingerprint: str,
        alertname: str,
        severity: str,
        service: str,
        namespace: str,
        summary: str,
        description: str,
        starts_at: datetime,
        labels: Dict[str, str],
        annotations: Dict[str, str],
        generator_url: Optional[str],
    ) -> Incident:
        """Handle a firing alert: deduplicate if active incident exists, else create new incident."""
        existing_incident = self.incident_repo.get_active_by_fingerprint(fingerprint)

        if existing_incident:
            logger.info(
                "Deduplicating alert %s to existing active incident %s",
                alertname,
                existing_incident.id,
            )
            if generator_url:
                annotations["generatorURL"] = generator_url

            # Create attached alert record
            alert_record = IncidentAlert(
                incident_id=existing_incident.id,
                fingerprint=fingerprint,
                alertname=alertname,
                status="firing",
                severity=severity,
                service=service,
                namespace=namespace,
                labels=labels,
                annotations=annotations,
                starts_at=starts_at,
            )
            self.incident_repo.add_alert(alert_record)

            # Record deduplicated event in timeline
            self.timeline_repo.record_event(
                incident_id=existing_incident.id,
                event_type="ALERT_DEDUPLICATED",
                summary=f"Subsequent alert notification received for {alertname}",
                details={"fingerprint": fingerprint, "labels": labels},
                actor="ALERTMANAGER",
            )
            return existing_incident

        # New incident creation
        incident_id = generate_incident_id()
        new_incident = Incident(
            id=incident_id,
            title=summary[:255],
            description=description,
            severity=severity,
            state=IncidentState.TRIGGERED.value,
            affected_service=service,
            namespace=namespace,
            fingerprint=fingerprint,
            source_alert=alertname,
            created_at=starts_at,
            updated_at=datetime.now(timezone.utc),
        )
        created_incident = self.incident_repo.create(new_incident)

        if generator_url:
            annotations["generatorURL"] = generator_url

        # Attach first alert
        first_alert = IncidentAlert(
            incident_id=incident_id,
            fingerprint=fingerprint,
            alertname=alertname,
            status="firing",
            severity=severity,
            service=service,
            namespace=namespace,
            labels=labels,
            annotations=annotations,
            starts_at=starts_at,
        )
        self.incident_repo.add_alert(first_alert)

        # Log timeline events
        self.timeline_repo.record_event(
            incident_id=incident_id,
            event_type="INCIDENT_TRIGGERED",
            summary=f"Incident opened: {summary}",
            details={"service": service, "severity": severity, "source_alert": alertname},
            actor="SYSTEM",
        )
        self.timeline_repo.record_event(
            incident_id=incident_id,
            event_type="ALERT_RECEIVED",
            summary=f"Alertmanager fired alert {alertname}",
            details={"fingerprint": fingerprint, "labels": labels, "annotations": annotations},
            actor="ALERTMANAGER",
        )

        logger.info(
            "Created new incident %s for alert %s on service %s",
            incident_id,
            alertname,
            service,
        )
        return created_incident

    def _handle_resolved_alert(
        self,
        fingerprint: str,
        alertname: str,
        ends_at: Optional[datetime],
        labels: Dict[str, str],
        annotations: Dict[str, str],
    ) -> Optional[Incident]:
        """Handle a resolved alert notification."""
        active_incident = self.incident_repo.get_active_by_fingerprint(fingerprint)
        if not active_incident:
            logger.info("Received resolution for fingerprint %s with no active incident", fingerprint)
            return None

        # Record resolution alert
        resolved_record = IncidentAlert(
            incident_id=active_incident.id,
            fingerprint=fingerprint,
            alertname=alertname,
            status="resolved",
            severity=active_incident.severity,
            service=active_incident.affected_service,
            namespace=active_incident.namespace,
            labels=labels,
            annotations=annotations,
            ends_at=ends_at or datetime.now(timezone.utc),
        )
        self.incident_repo.add_alert(resolved_record)

        self.timeline_repo.record_event(
            incident_id=active_incident.id,
            event_type="ALERT_RESOLVED",
            summary=f"Alertmanager reported alert {alertname} resolved",
            details={"fingerprint": fingerprint, "endsAt": ends_at.isoformat() if ends_at else None},
            actor="ALERTMANAGER",
        )
        logger.info(
            "Recorded resolution for alert %s on incident %s",
            alertname,
            active_incident.id,
        )
        return active_incident
