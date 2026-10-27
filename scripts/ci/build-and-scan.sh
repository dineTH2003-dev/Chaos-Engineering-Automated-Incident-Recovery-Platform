#!/usr/bin/env bash
# ResilienceLab — Automated Container Build and Trivy Security Scanner
# Usage: ./scripts/ci/build-and-scan.sh [--push] [--strict]

set -euo pipefail

REGISTRY_OWNER="${REGISTRY_OWNER:-dineth2003-dev}"
IMAGE_PREFIX="${IMAGE_PREFIX:-ghcr.io/${REGISTRY_OWNER}/resiliencelab}"
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'local')"
TIMESTAMP="$(date -u +%Y%m%d%H%M%S)"
STRICT_MODE=false
PUSH_MODE=false

for arg in "$@"; do
    case "$arg" in
        --strict) STRICT_MODE=true ;;
        --push) PUSH_MODE=true ;;
    esac
done

API_TAG="${GIT_SHA}"
WORKER_TAG="${GIT_SHA}"
API_IMAGE="${IMAGE_PREFIX}-api:${API_TAG}"
WORKER_IMAGE="${IMAGE_PREFIX}-worker:${WORKER_TAG}"
LOCAL_API="resiliencelab-api:${API_TAG}"
LOCAL_WORKER="resiliencelab-worker:${WORKER_TAG}"

echo "============================================================"
echo "ResilienceLab Container Build & Security Supply Chain"
echo "Commit SHA:     ${GIT_SHA}"
echo "API Image:      ${API_IMAGE}"
echo "Worker Image:   ${WORKER_IMAGE}"
echo "Strict Mode:    ${STRICT_MODE}"
echo "Push to GHCR:   ${PUSH_MODE}"
echo "============================================================"

# 1. Build Container Images
echo -e "\n[1/4] Building API Container Image..."
docker build \
    --label "org.opencontainers.image.revision=${GIT_SHA}" \
    --label "org.opencontainers.image.created=${TIMESTAMP}" \
    --label "org.opencontainers.image.source=https://github.com/dineTH2003-dev/Chaos-Engineering-Automated-Incident-Recovery-Platform" \
    -t "${LOCAL_API}" \
    -t "${API_IMAGE}" \
    ./application/api

echo -e "\n[2/4] Building Worker Container Image..."
docker build \
    --label "org.opencontainers.image.revision=${GIT_SHA}" \
    --label "org.opencontainers.image.created=${TIMESTAMP}" \
    --label "org.opencontainers.image.source=https://github.com/dineTH2003-dev/Chaos-Engineering-Automated-Incident-Recovery-Platform" \
    -t "${LOCAL_WORKER}" \
    -t "${WORKER_IMAGE}" \
    ./application/worker

echo "Containers built successfully."

# 2. Security Vulnerability Scanning with Trivy
TRIVY_BIN=""
if command -v trivy >/dev/null 2>&1; then
    TRIVY_BIN="trivy"
elif [ -x "${HOME}/.local/bin/trivy" ]; then
    TRIVY_BIN="${HOME}/.local/bin/trivy"
fi

SCAN_EXIT_CODE=0
if [ "${STRICT_MODE}" = true ]; then
    SCAN_EXIT_CODE=1
fi

echo -e "\n[3/4] Running Trivy Vulnerability Scan on API Image..."
if [ -n "${TRIVY_BIN}" ]; then
    "${TRIVY_BIN}" image \
        --severity HIGH,CRITICAL \
        --exit-code "${SCAN_EXIT_CODE}" \
        --ignore-unfixed \
        --ignorefile .trivyignore \
        "${LOCAL_API}" || {
            echo "WARNING: Vulnerability threshold breached in API image."
            if [ "${STRICT_MODE}" = true ]; then exit 1; fi
        }
else
    echo "Running Trivy via Docker container..."
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
        -v "$(pwd)/.trivyignore:/.trivyignore:ro" \
        aquasec/trivy:latest image \
        --severity HIGH,CRITICAL \
        --exit-code "${SCAN_EXIT_CODE}" \
        --ignore-unfixed \
        --ignorefile /.trivyignore \
        "${LOCAL_API}" || {
            echo "WARNING: Vulnerability threshold breached in API image."
            if [ "${STRICT_MODE}" = true ]; then exit 1; fi
        }
fi

echo -e "\n[4/4] Running Trivy Vulnerability Scan on Worker Image..."
if [ -n "${TRIVY_BIN}" ]; then
    "${TRIVY_BIN}" image \
        --severity HIGH,CRITICAL \
        --exit-code "${SCAN_EXIT_CODE}" \
        --ignore-unfixed \
        --ignorefile .trivyignore \
        "${LOCAL_WORKER}" || {
            echo "WARNING: Vulnerability threshold breached in Worker image."
            if [ "${STRICT_MODE}" = true ]; then exit 1; fi
        }
else
    echo "Running Trivy via Docker container..."
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
        -v "$(pwd)/.trivyignore:/.trivyignore:ro" \
        aquasec/trivy:latest image \
        --severity HIGH,CRITICAL \
        --exit-code "${SCAN_EXIT_CODE}" \
        --ignore-unfixed \
        --ignorefile /.trivyignore \
        "${LOCAL_WORKER}" || {
            echo "WARNING: Vulnerability threshold breached in Worker image."
            if [ "${STRICT_MODE}" = true ]; then exit 1; fi
        }
fi

# 3. Optional Push to Registry
if [ "${PUSH_MODE}" = true ]; then
    echo -e "\nPushing images to GHCR..."
    docker push "${API_IMAGE}"
    docker push "${WORKER_IMAGE}"
    echo "Pushed: ${API_IMAGE}"
    echo "Pushed: ${WORKER_IMAGE}"
fi

echo -e "\n=== Supply Chain Build & Scan Completed Successfully ==="
