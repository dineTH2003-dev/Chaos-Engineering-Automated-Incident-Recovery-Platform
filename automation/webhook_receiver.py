"""ResilienceLab Alertmanager Webhook Receiver.

Receives, parses, and records Prometheus Alertmanager webhook notifications
for incident tracking, MTTD/MTTR calculations, and automated recovery.
"""

from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel

LOG_DIR = os.getenv("INCIDENTS_LOG_DIR", "/reports/incidents")
LOG_FILE = os.path.join(LOG_DIR, "incidents.log")

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [webhook-receiver] %(message)s",
)
logger = logging.getLogger("webhook-receiver")

app = FastAPI(
    title="ResilienceLab Incident Webhook Receiver",
    description="Processes and persists Alertmanager alerts for incident management",
    version="1.0.0",
)

# In-memory incident event buffer for querying and verification
incident_events: List[Dict[str, Any]] = []


def ensure_log_directory() -> None:
    """Ensure the reports/incidents directory exists."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception as exc:
        logger.warning("Could not create log directory %s: %s", LOG_DIR, exc)


def append_to_incident_log(record: Dict[str, Any]) -> None:
    """Append a structured JSON record to the incident log file."""
    ensure_log_directory()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.error("Failed to write to incident log file %s: %s", LOG_FILE, exc)


@app.get("/health", tags=["system"], summary="Health check")
def health() -> Dict[str, str]:
    """Liveness & readiness probe endpoint."""
    return {"status": "healthy", "service": "webhook-receiver"}


@app.get("/alerts", tags=["incidents"], summary="List recorded alert events")
def list_alerts() -> List[Dict[str, Any]]:
    """Return all recorded incident events received by this webhook."""
    return incident_events


@app.delete("/alerts", tags=["incidents"], summary="Clear recorded alert events")
def clear_alerts() -> Dict[str, str]:
    """Clear in-memory incident event buffer (used between test runs)."""
    incident_events.clear()
    return {"status": "cleared"}


@app.post("/alerts", tags=["webhook"], summary="Receive Alertmanager alerts")
async def receive_alerts(request: Request) -> Dict[str, Any]:
    """Receive and process webhook payload from Alertmanager."""
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception as exc:
        logger.error("Failed to parse incoming webhook JSON: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    status_state = payload.get("status", "unknown")
    alerts = payload.get("alerts", [])
    logger.info("Received Alertmanager notification with status '%s' (%d alerts)", status_state, len(alerts))

    processed_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        record = {
            "received_at": now_iso,
            "status": alert.get("status", status_state),
            "alertname": labels.get("alertname", "UnknownAlert"),
            "severity": labels.get("severity", "unknown"),
            "namespace": labels.get("namespace", "unknown"),
            "service": labels.get("service") or labels.get("app") or labels.get("job") or "unknown",
            "pod": labels.get("pod", "unknown"),
            "summary": annotations.get("summary", ""),
            "description": annotations.get("description", ""),
            "startsAt": alert.get("startsAt", ""),
            "endsAt": alert.get("endsAt", ""),
            "fingerprint": alert.get("fingerprint", ""),
            "raw_labels": labels,
        }

        incident_events.append(record)
        append_to_incident_log(record)
        processed_count += 1

        logger.info(
            "Recorded incident event: [%s] alert=%s severity=%s service=%s pod=%s",
            record["status"].upper(),
            record["alertname"],
            record["severity"],
            record["service"],
            record["pod"],
        )

    return {
        "status": "processed",
        "alerts_processed": processed_count,
        "total_recorded": len(incident_events),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)
