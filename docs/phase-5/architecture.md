# ResilienceLab Phase 5: GitOps-Driven CI/CD & SRE-Aware Progressive Delivery Architecture

## 1. Executive Summary

ResilienceLab Phase 5 extends the platform from an automated incident response system (Phase 4) into a production-grade, GitOps-driven continuous delivery ecosystem. Traditional CI/CD pipelines often push code directly to Kubernetes clusters using high-privileged service account keys, lack automated health validation, and fail to incorporate Site Reliability Engineering (SRE) principles such as Service Level Objectives (SLOs) and error budget consumption into deployment decisions.

Phase 5 introduces:
1. **Declarative GitOps Reconciliation**: Utilizing Flux CD v2 (`source-controller`, `kustomize-controller`, `helm-controller`) to guarantee that the Kubernetes cluster continuously matches the desired state declared in Git.
2. **Automated Drift Detection & Self-Healing**: Out-of-band cluster modifications (e.g., ad-hoc `kubectl edit` or manual replica scaling) are automatically detected and rectified within seconds.
3. **Hardened Container Supply Chain**: Deterministic, immutable image tagging via Git commit SHAs, vulnerability gates enforced by Trivy with zero tolerance for unmitigated HIGH or CRITICAL CVEs, and OCI image metadata standards.
4. **SRE Deployment Health Verification Engine**: Automated multi-stage rollout verification combining Kubernetes native status checks, pod readiness/restart counts, multi-sample HTTP health/readiness probe streaks, and Prometheus 5xx error rate and tail latency telemetry.
5. **SRE-Aware SLO Release Gates**: Release progression governed by live SLO error budget burn rates (14.4x fast burn detection), rolling availability targets (99.5%), tail latency targets (500ms p95), and automated active incident cross-referencing.
6. **GitOps-Compatible Rollback & Incident Escalation**: Autonomous rollbacks that revert desired Git state, trigger immediate reconciliation, verify service restoration, and file an incident with postmortem tracking in the Phase 4 Incident Response Platform.

---

## 2. High-Level Architecture Diagram

```
+---------------------------------------------------------------------------------------------------+
|                                   DEVELOPER / GIT REPOSITORY                                     |
|  - Git Commit (SHA)                                                                               |
|  - Pull Request / Push to main                                                                    |
+-------------------------------------------------+-------------------------------------------------+
                                                  |
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                     GITHUB ACTIONS CI PIPELINE                                    |
|  - Job 1: Flake8 Linter & Static YAML Syntax Validation                                           |
|  - Job 2: Automated Unit & Integration Tests (API + Phase 4 Incident + Phase 5 Delivery)          |
|  - Job 3: Helm Chart Lint & Template Rendering (`helm lint`, `helm template`)                     |
|  - Job 4: Container Build & Trivy Security Scan (Block on HIGH/CRITICAL CVEs)                      |
+-------------------------------------------------+-------------------------------------------------+
                                                  | Pass
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                     GITOPS REPOSITORY STATE                                       |
|  - `deployments/gitops/apps/resiliencelab/helmrelease.yaml`                                      |
|    * Updated image tag: `c49d3e3`                                                                 |
|    * Drift detection enabled: `driftDetection.mode: enabled`                                      |
|  - Commit pushed to `origin main`                                                                 |
+-------------------------------------------------+-------------------------------------------------+
                                                  | Pull / Webhook Reconcile
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                       FLUX CD CONTROLLERS                                         |
|  - `source-controller` (13 MiB): Syncs GitRepository CRD                                          |
|  - `helm-controller` (9 MiB): Evaluates HelmRelease CRD, detects drift, reconciles Helm release   |
+-------------------------------------------------+-------------------------------------------------+
                                                  | Declarative Apply
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                KUBERNETES CLUSTER (MINIKUBE)                                      |
|  Namespace: `resiliencelab`                                                                       |
|  - Deployment `api` (Replicas: 2, RollingUpdate maxSurge: 1, maxUnavailable: 0)                  |
|  - Deployment `worker` (Replicas: 1, RollingUpdate)                                               |
|  - Deployment `postgres` (Replicas: 1, Recreate, PVC `postgres-pvc` preserved)                    |
|  - Deployment `webhook-receiver` (Replicas: 1, Incident Response Platform)                        |
|  - HorizontalPodAutoscaler `api-hpa` (Min: 2, Max: 4, Target CPU: 75%)                            |
|  - ServiceMonitors: Prometheus metric scrapers for `api` and `worker`                             |
+-------------------------------------------------+-------------------------------------------------+
                                                  |
                         +------------------------+------------------------+
                         |                                                 |
                         v                                                 v
+--------------------------------------------------+ +----------------------------------------------+
|       DEPLOYMENT HEALTH VERIFIER ENGINE          | |            SRE SLO RELEASE GATES             |
|  1. Kubernetes Rollout Status                    | |  1. Active Phase 4 Incident Check            |
|  2. Pod Readiness & Restart Drift Detection      | |  2. Traffic Volume & Statistical Confidence  |
|  3. Consecutive HTTP Health Probes (/health)     | |  3. 99.5% Availability SLI Target            |
|  4. Prometheus Telemetry (5xx Rate, p95 Latency) | |  4. Tail Latency Target (p95 < 500ms)        |
|                                                  | |  5. Error Budget Burn Rate Gate (14.4x 1h)   |
+------------------------+-------------------------+ +---------------------+------------------------+
                         |                                                 |
                         +------------------------+------------------------+
                                                  |
                                                  v
                         +-------------------------------------------------+
                         |          PROGRESSIVE DELIVERY DECISION          |
                         +------------------------+------------------------+
                                                  |
                         +------------------------+------------------------+
                         | Pass                                            | Fail
                         v                                                 v
+--------------------------------------------------+ +----------------------------------------------+
|                 PROMOTE RELEASE                  | |             AUTOMATED ROLLBACK               |
|  - Finalize rollout in deployment log            | |  1. Acquire mutual exclusion lock file       |
|  - Metric recording for DORA Lead Time & MTTR    | |  2. Revert GitOps manifest to healthy tag    |
|  - Zero incident escalation                      | |  3. Force immediate Flux reconciliation      |
|                                                  | |  4. Independently verify restored health     |
|                                                  | |  5. Emit webhook to Phase 4 Incident Engine  |
|                                                  | |  6. Generate Incident Postmortem Report      |
+--------------------------------------------------+ +----------------------------------------------+
```

---

## 3. Core Architectural Subsystems

### 3.1 Declarative Helm Packaging (`deployments/helm/resiliencelab/`)
To eliminate configuration drift and redundant manifest declarations across environments, the entire platform was unified into a single production-grade Helm chart:
- **Parameterized Services**: `api`, `worker`, `postgres`, `webhook-receiver`.
- **Environment Overlays**: `deployments/helm/environments/development/values.yaml` and `staging/values.yaml`.
- **Stateful Resource Protection**: The PostgreSQL PVC is annotated with `"helm.sh/resource-policy": keep` to prevent accidental volume teardown or data loss during Helm upgrades or rollbacks.
- **Observability Declarations**: `ServiceMonitor` resources are directly packaged within the chart, binding application endpoints to Prometheus without ad-hoc operator intervention.

### 3.2 Flux CD GitOps Engine (`deployments/gitops/`)
The GitOps engine runs directly within the Minikube cluster:
- **Controllers**:
  - `source-controller`: Monitors Git repository commits on `main`.
  - `kustomize-controller`: Applies infrastructure components and CRDs.
  - `helm-controller`: Manages Helm releases, chart unpacks, values merges, and drift healing.
- **Resource Budgeting**:
  - Combined memory consumption of all 3 Flux controllers is capped at **~31 MiB** under normal operations (requests: 50m CPU / 64Mi RAM; limits: 300m CPU / 256Mi RAM per controller).
  - Preserves over 1.1 GiB of Minikube headroom for application workloads and Chaos Mesh experiments.
- **Drift Detection & Healing**:
  - Configured with `spec.driftDetection.mode: enabled`.
  - Continuous reconciliation cycle verifies that live Kubernetes resource states strictly match the Git repository. Manual out-of-band scaling or edits are automatically reverted.

### 3.3 SRE Deployment Health Verification (`automation/deployment/verifier.py`)
Rather than relying solely on `kubectl rollout status` (which only checks pod readiness gates), the Verifier performs a 4-step verification sequence:
1. **K8s Rollout Completion**: Validates that deployment rollout status completes within 120 seconds.
2. **Pod Readiness & Zero Crash Restarts**: Validates that all desired replicas are ready and that restart counts on new pods equal zero.
3. **Consecutive Probe Streaks**: Executes 3 consecutive HTTP `/health` and `/ready` probes separated by 2-second intervals. Any single failure resets the streak counter to 0.
4. **Prometheus Telemetry Check**: Queries Prometheus directly to evaluate HTTP 5xx error percentage (< 1.0%) and tail p95 latency (< 500ms).

### 3.4 SRE-Aware SLO Release Gates (`automation/deployment/release_gate.py`)
Deployments are evaluated against real-time reliability indicators before progressive rollout proceeds:
1. **Active Incidents**: Queries Phase 4 Incident Engine (`http://localhost:5001/api/incidents`); releases are blocked if any active CRITICAL or WARNING incidents exist.
2. **Statistical Confidence**: Checks request volume (`sum(rate(http_requests_total[5m]))`). Releases require at least 10 RPS for full statistical confidence, falling back to conservative verification under lower traffic.
3. **Availability Objective**: Enforces 99.5% availability target over a 5-minute rolling window.
4. **Tail Latency Objective**: Enforces p95 latency < 500ms.
5. **Error Budget Fast-Burn Rate**: Calculates 1-hour error budget burn rate. A burn rate $\ge 14.4\times$ immediately triggers an `ABORT` gate decision.

### 3.5 Automated GitOps Rollback & Incident Escalation (`automation/deployment/rollback.py`)
When a release fails verification or breaches SLO release gates:
1. **Mutual Exclusion**: Acquires `/tmp/resiliencelab_deployment.lock` to prevent race conditions during rapid concurrent deployment events.
2. **GitOps State Reversion**: Updates `deployments/gitops/apps/resiliencelab/helmrelease.yaml` to point back to the previous known-healthy image tag recorded in `reports/deployments/deployments.log`.
3. **Immediate Reconciliation**: Invokes `flux reconcile helmrelease` to bypass polling latency and initiate immediate pod rollback.
4. **Independent Post-Rollback Verification**: Executes health verification against the restored revision.
5. **Phase 4 Incident Engine Escalation**: Transmits a structured webhook alert to `http://localhost:5001/api/alerts`, creating an incident, triggering runbook automation, and producing an SRE postmortem.

---

## 4. Cross-Phase Integration Matrix

| Integration Point | Connected Phase | Description |
|---|---|---|
| **Database PVC & Order Integrity** | Phase 1 | Helm chart preserves `postgres-pvc` with `"helm.sh/resource-policy": keep`. 1,468 historical orders remain intact. |
| **Telemetry & ServiceMonitors** | Phase 2 | Helm chart templates `ServiceMonitor` resources; Verifier & Release Gates query Prometheus on port 9091. |
| **Chaos Experiment Protection** | Phase 3 | Rollback lock and release gates abort new deployments if active chaos injection causes SLO degradation. |
| **Incident Response Engine** | Phase 4 | Release Gates check active incidents via port 5001; failed rollbacks trigger automated Phase 4 incident lifecycle. |
| **Grafana DORA Dashboard** | Phase 2 & 5 | Dashboard 7 tracks deployment frequency, change failure rate, MTTR, and Flux reconciliation sync status. |

---

## 5. Architectural Verification & Guarantees

1. **Zero Data Loss**: Validated across all deployment and rollback scenarios.
2. **Determinism**: Every container image is linked to an exact Git commit SHA.
3. **Auditability**: Every deployment event, configuration diff, and gate decision is durably logged in `reports/deployments/deployments.log` and JSON artifacts.
4. **Self-Healing**: Git is the single source of truth; any cluster drift is automatically healed by Flux CD.
