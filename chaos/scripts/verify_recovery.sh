#!/usr/bin/env bash
set -euo pipefail

# ResilienceLab — Post-Experiment Verification Script
NAMESPACE="resiliencelab"

echo "=========================================================="
echo "Verifying Cluster Steady-State Recovery in '${NAMESPACE}'"
echo "=========================================================="

FAILED=0

# 1. Verify No Active Chaos
echo -n "Checking for active Chaos resources... "
ACTIVE_CHAOS=$(kubectl get podchaos,networkchaos,stresschaos -n "${NAMESPACE}" --no-headers 2>/dev/null | wc -l)
if [ "${ACTIVE_CHAOS}" -eq 0 ]; then
    echo "PASS (0 active chaos resources)"
else
    echo "FAIL (${ACTIVE_CHAOS} active chaos resources still present)"
    FAILED=1
fi

# 2. Verify Deployments
echo -n "Checking API Deployment (desired: 2 replicas)... "
API_READY=$(kubectl get deployment api -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}')
if [ "${API_READY}" -ge 2 ]; then
    echo "PASS (${API_READY} ready replicas)"
else
    echo "FAIL (${API_READY} ready replicas, expected >= 2)"
    FAILED=1
fi

echo -n "Checking PostgreSQL Deployment (desired: 1 replica)... "
PG_READY=$(kubectl get deployment postgres -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}')
if [ "${PG_READY}" -ge 1 ]; then
    echo "PASS (${PG_READY} ready replica)"
else
    echo "FAIL (${PG_READY} ready replicas, expected >= 1)"
    FAILED=1
fi

# 3. Verify Health Probes
echo -n "Checking API /health probe... "
HEALTH_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health || echo "000")
if [ "${HEALTH_CODE}" -eq 200 ]; then
    echo "PASS (HTTP 200)"
else
    echo "FAIL (HTTP ${HEALTH_CODE})"
    FAILED=1
fi

echo -n "Checking API /ready probe (with database check)... "
READY_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ready || echo "000")
if [ "${READY_CODE}" -eq 200 ]; then
    echo "PASS (HTTP 200)"
else
    echo "FAIL (HTTP ${READY_CODE})"
    FAILED=1
fi

echo "=========================================================="
if [ "${FAILED}" -eq 0 ]; then
    echo "Cluster is in fully recovered steady-state."
    exit 0
else
    echo "Recovery verification FAILED."
    exit 1
fi
