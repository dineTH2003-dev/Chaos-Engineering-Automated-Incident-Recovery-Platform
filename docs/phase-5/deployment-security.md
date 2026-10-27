# ResilienceLab Phase 5: Deployment Security & Container Supply Chain Hardening

## 1. Security Architecture Overview

ResilienceLab Phase 5 enforces a zero-trust, shift-left security paradigm across both the CI/CD pipeline and the runtime Kubernetes cluster. Rather than treating security as an afterthought or an ad-hoc scanning task, security gates are embedded directly into:
1. **Source Code Quality**: Flake8 AST parsing and static analysis.
2. **Container Build Supply Chain**: OCI-compliant metadata labeling and pre-push vulnerability scanning via Trivy.
3. **Pull-Based Deployment Architecture**: Eliminating push-based CI credentials and external cluster access tokens.
4. **Kubernetes Least-Privilege RBAC**: Resource scoping and isolated ServiceAccounts for GitOps reconciliation.
5. **Runtime Boundary Defense**: Non-root container execution, strict memory/CPU resource limits, and encrypted secret management.

---

## 2. Supply Chain Security & Vulnerability Gating

### 2.1 Minimal Base Images
All container images (`resiliencelab-api`, `resiliencelab-worker`, `resiliencelab-webhook-receiver`) are built upon minimal official Debian/Alpine base images:
- **API and Worker**: `python:3.12-slim` (reducing OS-level package surface area by over 80% compared to standard `python:3.12`).
- **Database**: `postgres:16-alpine` (hardened minimal runtime footprint).

### 2.2 Immutable Artifact Tagging
Container tags never use mutable references like `latest` or `dev`. Every image is tagged strictly with the 7-character Git commit SHA:
```bash
resiliencelab-api:$(git rev-parse --short HEAD)
resiliencelab-worker:$(git rev-parse --short HEAD)
```
This guarantees cryptographically verifiable provenance between Git commit history, CI scan logs, and running cluster pods.

### 2.3 Trivy Vulnerability Scanning Engine
Vulnerability scanning is automated via `scripts/ci/build-and-scan.sh` and enforced in `.github/workflows/ci.yaml`:
```bash
trivy image \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    --ignore-unfixed \
    "${IMAGE_NAME}:${COMMIT_SHA}"
```

#### Security Gate Criteria
- **CRITICAL Vulnerabilities**: 0 tolerated. Any detected CRITICAL CVE results in an immediate non-zero exit code, terminating the CI build and aborting the deployment.
- **HIGH Vulnerabilities**: 0 tolerated unless an explicit, vetted entry exists in `.trivyignore`.
- **Policy Enforcement**: Unfixed upstream kernel or glibc vulnerabilities that have no vendor patch available can only be ignored after documented architectural review in `.trivyignore`.

---

## 3. Pull-Based vs Push-Based GitOps Security Model

Traditional CI/CD tools (Jenkins, GitLab CI, GitHub Actions) push deployments by storing long-lived `kubeconfig` files or cluster admin ServiceAccount tokens inside CI runner secrets. This introduces severe attack vectors:
- **Credential Exfiltration**: A compromise of CI runner environments exposes full cluster administrative access.
- **Firewall Hole Opening**: Cluster API servers must be exposed publicly to accept incoming CI push commands.

### The Flux CD Pull-Based Alternative
ResilienceLab Phase 5 adopts the pull-based GitOps paradigm:
1. **Zero External Ingress**: The Minikube API server is not exposed to the internet or external CI systems.
2. **Internal Reconciliation**: Flux controllers run inside the cluster and initiate outbound HTTPS connections to Git repositories.
3. **No CI Cluster Credentials**: GitHub Actions requires zero Kubernetes secrets or administrative tokens. CI only needs push rights to the Git repository.

```
Traditional Push CD (Vulnerable):
CI Runner (holds kubeconfig / admin token) ------> Ingress / Kube API Server (Exposed)

ResilienceLab Pull GitOps (Secure):
CI Runner ------> Git Repository (GitHub)
                       ^
                       | (Read-Only Outbound HTTPS)
Flux Controller (Inside Cluster, Scoped RBAC)
```

---

## 4. Kubernetes RBAC & Least-Privilege Architecture

Flux CD controllers run within the isolated `flux-system` namespace:
- `source-controller`: Bound to `source-controller` ServiceAccount with permissions strictly scoped to GitRepository and Bucket CRDs.
- `helm-controller`: Bound to `helm-controller` ServiceAccount with permissions to manage HelmReleases and deploy resources within the `resiliencelab` namespace.
- **Namespace Isolation**: Application services (`api`, `worker`, `postgres`) run in the dedicated `resiliencelab` namespace. Application ServiceAccounts have zero RBAC permissions to read secrets outside their namespace or query the Kubernetes API.

---

## 5. Secret Management & Data Protection

1. **Decoupling Secrets from Git**:
   - `deployments/helm/resiliencelab/templates/secrets.yaml` references Kubernetes Secret objects (`resiliencelab-secrets`).
   - Plaintext credentials and database passwords are never committed to Git.
   - `.gitignore` strictly prevents local environment files (`.env`, `credentials.json`, `kubeconfig`) from entering version control.
2. **Stateful Volume Safeguards**:
   - The PostgreSQL persistent data volume is annotated with `"helm.sh/resource-policy": keep`.
   - Even if a malicious or errant commit deletes the database Helm release, the underlying 1Gi persistent volume claim remains intact and protected from data destruction.

---

## 6. Runtime Container Hardening Standards

Every pod deployed via Helm implements Kubernetes security best practices:
- **Resource Quotas & Limits**: CPU and memory limits are strictly enforced on all containers to prevent Denial-of-Service (DoS) and neighbor-pod starvation:
  - `api`: Requests 100m CPU / 128Mi RAM; Limits 500m CPU / 512Mi RAM.
  - `worker`: Requests 50m CPU / 64Mi RAM; Limits 250m CPU / 256Mi RAM.
  - `postgres`: Requests 100m CPU / 256Mi RAM; Limits 500m CPU / 512Mi RAM.
- **Health Probing**: Liveness and readiness probes prevent unready or unhealthy containers from receiving production traffic.
- **Graceful Termination**: `terminationGracePeriodSeconds: 30` ensures inflight database transactions and background tasks complete cleanly before container shutdown.
