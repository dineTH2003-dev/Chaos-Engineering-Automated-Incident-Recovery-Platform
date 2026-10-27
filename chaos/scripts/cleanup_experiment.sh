#!/usr/bin/env bash
set -euo pipefail

# ResilienceLab — Emergency Cleanup & Abort Script
NAMESPACE="resiliencelab"

echo "=========================================================="
echo "Cleaning up all active Chaos Mesh resources in '${NAMESPACE}'"
echo "=========================================================="

# 1. Delete all Chaos Mesh resources
kubectl delete podchaos,networkchaos,stresschaos,iochaos,httpchaos,dnschaos --all -n "${NAMESPACE}" --ignore-not-found=true --timeout=15s || true

# 2. Reset deployment replica counts to steady-state
echo "Verifying steady-state replica scales..."
kubectl scale deployment api -n "${NAMESPACE}" --replicas=2
kubectl scale deployment postgres -n "${NAMESPACE}" --replicas=1
kubectl scale deployment worker -n "${NAMESPACE}" --replicas=1

# 3. Wait for rollout
echo "Waiting for workloads to reach Ready state..."
kubectl rollout status deployment/api -n "${NAMESPACE}" --timeout=60s
kubectl rollout status deployment/postgres -n "${NAMESPACE}" --timeout=60s

echo "Cleanup completed successfully."
