"""
ResilienceLab — Webhook Receiver & Incident Response Engine Entrypoint
Maintains 100% backward compatibility with Phase 2/3 Webhook Receiver while
delegating all operations to the Phase 4 Incident Response Platform.
"""

import logging
import os
import uvicorn

# Import the full Phase 4 FastAPI ASGI application
from automation.incident.api.main import app
from automation.incident.services.ingestion_service import (
    get_raw_alerts_buffer as _get_alerts,
    clear_raw_alerts_buffer as _clear_alerts,
)

logger = logging.getLogger("webhook-receiver")

# In-memory buffer reference for direct script access if needed
incident_events = _get_alerts()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    logger.info("Starting ResilienceLab Incident Response Engine on port %d...", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
