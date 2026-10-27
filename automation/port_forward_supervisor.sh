#!/usr/bin/env bash
# ResilienceLab — Port Forward Supervisor
# Automatically reconnects port-forwarding to surviving service replicas during chaos experiments.

set -u

echo "Starting Port Forward Supervisor for api-service (8000:80)..."
while true; do
    kubectl port-forward -n resiliencelab service/api-service 8000:80 >/dev/null 2>&1 || true
    sleep 0.2
done
