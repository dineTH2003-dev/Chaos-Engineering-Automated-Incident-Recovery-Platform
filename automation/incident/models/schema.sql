-- ResilienceLab Incident Response Platform — PostgreSQL Schema
-- Namespace: resiliencelab
-- Preserves existing 'orders' table.

-- 1. Incidents Table
CREATE TABLE IF NOT EXISTS incidents (
    id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    severity VARCHAR(20) NOT NULL DEFAULT 'warning',
    state VARCHAR(30) NOT NULL DEFAULT 'TRIGGERED',
    affected_service VARCHAR(100) NOT NULL,
    namespace VARCHAR(50) NOT NULL DEFAULT 'resiliencelab',
    fingerprint VARCHAR(100) NOT NULL,
    source_alert VARCHAR(100) NOT NULL,
    suspected_cause TEXT,
    confidence VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    resolved_at TIMESTAMP WITH TIME ZONE,
    extra_data JSONB
);

CREATE INDEX IF NOT EXISTS idx_incidents_state ON incidents(state);
CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents(affected_service);
CREATE INDEX IF NOT EXISTS idx_incidents_fingerprint ON incidents(fingerprint);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at);

-- 2. Incident Alerts Table
CREATE TABLE IF NOT EXISTS incident_alerts (
    id SERIAL PRIMARY KEY,
    incident_id VARCHAR(50) NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    fingerprint VARCHAR(100) NOT NULL,
    alertname VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    service VARCHAR(100),
    pod VARCHAR(100),
    namespace VARCHAR(50),
    summary TEXT,
    description TEXT,
    labels JSONB,
    annotations JSONB,
    starts_at TIMESTAMP WITH TIME ZONE,
    ends_at TIMESTAMP WITH TIME ZONE,
    received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incident_alerts_incident_id ON incident_alerts(incident_id);
CREATE INDEX IF NOT EXISTS idx_incident_alerts_fingerprint ON incident_alerts(fingerprint);

-- 3. Incident Timeline Table
CREATE TABLE IF NOT EXISTS incident_timeline (
    id SERIAL PRIMARY KEY,
    incident_id VARCHAR(50) NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    summary VARCHAR(255) NOT NULL,
    details JSONB,
    actor VARCHAR(100) NOT NULL DEFAULT 'SYSTEM'
);

CREATE INDEX IF NOT EXISTS idx_incident_timeline_incident_id ON incident_timeline(incident_id);
CREATE INDEX IF NOT EXISTS idx_incident_timeline_timestamp ON incident_timeline(timestamp);

-- 4. Remediations Table
CREATE TABLE IF NOT EXISTS remediations (
    id VARCHAR(50) PRIMARY KEY,
    incident_id VARCHAR(50) NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    runbook_id VARCHAR(100),
    action_type VARCHAR(50) NOT NULL,
    target_resource VARCHAR(100) NOT NULL,
    namespace VARCHAR(50) NOT NULL DEFAULT 'resiliencelab',
    parameters JSONB,
    risk_level VARCHAR(20) NOT NULL DEFAULT 'LOW',
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    state VARCHAR(30) NOT NULL DEFAULT 'PROPOSED',
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    execution_output TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    executed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_remediations_incident_id ON remediations(incident_id);
CREATE INDEX IF NOT EXISTS idx_remediations_state ON remediations(state);

-- 5. Approvals Table
CREATE TABLE IF NOT EXISTS approvals (
    id VARCHAR(50) PRIMARY KEY,
    incident_id VARCHAR(50) NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    remediation_id VARCHAR(50) NOT NULL REFERENCES remediations(id) ON DELETE CASCADE,
    action VARCHAR(100) NOT NULL,
    reason TEXT NOT NULL,
    risk_level VARCHAR(20) NOT NULL,
    approver VARCHAR(100),
    decision VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    decision_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    decided_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_approvals_incident_id ON approvals(incident_id);
CREATE INDEX IF NOT EXISTS idx_approvals_decision ON approvals(decision);
