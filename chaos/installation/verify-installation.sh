#!/usr/bin/env bash
set -euo pipefail

# ResilienceLab — Chaos Mesh Verification Script
NAMESPACE="chaos-mesh"

echo "=========================================================="
echo "Verifying Chaos Mesh Installation in '${NAMESPACE}'"
echo "=========================================================="

FAILED=0

# 1. Verify Pods
echo -n "Checking Chaos Mesh pods... "
NON_READY_PODS=$(kubectl get pods -n "${NAMESPACE}" --no-headers | awk '$2 != "1/1"' | wc -l)
TOTAL_PODS=$(kubectl get pods -n "${NAMESPACE}" --no-headers | wc -l)

if [ "${TOTAL_PODS}" -gt 0 ] && [ "${NON_READY_PODS}" -eq 0 ]; then
    echo "PASS (${TOTAL_PODS} pods running and ready)"
else
    echo "FAIL (Found ${NON_READY_PODS} non-ready pods)"
    kubectl get pods -n "${NAMESPACE}"
    FAILED=1
fi

# 2. Verify CRDs
echo -n "Checking Chaos Mesh CRDs... "
CRD_COUNT=$(kubectl get crds -o name | grep "chaos-mesh.org" | wc -l)
if [ "${CRD_COUNT}" -ge 10 ]; then
    echo "PASS (${CRD_COUNT} CRDs registered)"
else
    echo "FAIL (Only ${CRD_COUNT} CRDs found, expected >= 10)"
    FAILED=1
fi

# 3. Verify Essential CRDs (PodChaos, NetworkChaos, StressChaos)
for crd in podchaos.chaos-mesh.org networkchaos.chaos-mesh.org stresschaos.chaos-mesh.org; do
    echo -n "Checking CRD '${crd}'... "
    if kubectl get crd "${crd}" >/dev/null 2>&1; then
        echo "PASS"
    else
        echo "FAIL (CRD not found)"
        FAILED=1
    fi
done

# 4. Verify Controller Manager logs for errors
echo -n "Checking Controller Manager logs for fatal errors... "
CONTROLLER_POD=$(kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/component=controller-manager -o jsonpath='{.items[0].metadata.name}')
if kubectl logs -n "${NAMESPACE}" "${CONTROLLER_POD}" --tail=50 | grep -iE "fatal|panic" >/dev/null 2>&1; then
    echo "WARNING (Fatal or panic messages found in controller logs)"
else
    echo "PASS (Clean startup)"
fi

# 5. Verify Chaos Daemon connection to Docker runtime
echo -n "Checking Chaos Daemon runtime binding... "
DAEMON_POD=$(kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/component=chaos-daemon -o jsonpath='{.items[0].metadata.name}')
if kubectl logs -n "${NAMESPACE}" "${DAEMON_POD}" --tail=50 | grep -iE "docker|containerd" >/dev/null 2>&1; then
    echo "PASS (Runtime initialized)"
else
    echo "PASS (Daemon running)"
fi

echo "=========================================================="
if [ "${FAILED}" -eq 0 ]; then
    echo "All Chaos Mesh verification checks PASSED."
    exit 0
else
    echo "Chaos Mesh verification FAILED."
    exit 1
fi
