#!/usr/bin/env bash
set -euo pipefail

# ResilienceLab — Chaos Mesh Installation Script
# Pinned version: v2.8.6
# Target Namespace: chaos-mesh

CHART_VERSION="2.8.6"
NAMESPACE="chaos-mesh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALUES_FILE="${SCRIPT_DIR}/values.yaml"

echo "=========================================================="
echo "Installing Chaos Mesh (v${CHART_VERSION}) into '${NAMESPACE}'"
echo "=========================================================="

# Ensure helm repository is added and updated
if ! helm repo list | grep -q "chaos-mesh"; then
    echo "Adding chaos-mesh Helm repository..."
    helm repo add chaos-mesh https://charts.chaos-mesh.org
fi

echo "Updating Helm repositories..."
helm repo update chaos-mesh

# Create namespace if not exists
if ! kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1; then
    echo "Creating namespace '${NAMESPACE}'..."
    kubectl create namespace "${NAMESPACE}"
fi

# Deploy Chaos Mesh via Helm
echo "Deploying Chaos Mesh chart with custom values..."
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
    --namespace "${NAMESPACE}" \
    --version "${CHART_VERSION}" \
    -f "${VALUES_FILE}"

echo "Waiting for Chaos Mesh controller and daemon to be Ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/instance=chaos-mesh -n "${NAMESPACE}" --timeout=180s

echo "Chaos Mesh installation completed successfully."
"${SCRIPT_DIR}/verify-installation.sh"
