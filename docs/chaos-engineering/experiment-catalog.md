# ResilienceLab — Chaos Engineering Experiment Catalog

This catalog documents the automated fault injection experiments implemented in ResilienceLab. Each experiment follows the SRE hypothesis-driven methodology:
**Steady State Hypothesis -> Fault Injection -> Telemetry Observation -> Automated Recovery -> Post-Chaos Verification**.

---

## Experiment Summary Table

| ID | Name | Fault Injection Type | Target Workload | Duration | Steady-State SLO Target | Primary Alerts Monitored |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **CE-01** | API Pod Failure | `PodChaos` (`pod-kill`) | `resiliencelab-api` (1 pod) | Instant | Availability >= 99.5%, Dropped requests = 0 | `KubePodNotReady`, `PodCrashLooping` |
| **CE-02** | API-DB Latency | `NetworkChaos` (`delay`) | `resiliencelab-api` -> `postgres` | 60s | Error rate < 1%, Latency elevated predictably | `HighRequestLatency` |
| **CE-03** | Database Outage | Controlled Replica Scale / Partition | `postgres` | 45s | Graceful 503 on `/ready`, 0 data loss on PVC | `PostgresDown`, `DatabaseConnectionFailures` |
| **CE-04** | CPU Saturation | `StressChaos` (`cpu-stress`) | `resiliencelab-api` (1 pod) | 60s | Availability >= 99.0%, CPU telemetry observable | `HighCPUUsage`, HPA Scale-up |

---

## Detailed Specifications

### CE-01: API Pod Failure & Kubernetes Self-Healing

- **Objective**: Validate that Kubernetes Deployment self-healing replaces a terminated API pod without dropping user traffic, leveraging 2-replica redundancy and Kubernetes Service load balancing.
- **Manifest**: `chaos/experiments/ce01-pod-failure.yaml`
- **Fault Type**: Chaos Mesh `PodChaos`
- **Action**: `pod-kill`
- **Selector**:
  - Namespace: `resiliencelab`
  - Labels: `app: resiliencelab-api`, `chaos-enabled: "true"`
  - Mode: `one`
- **Hypothesis**:
  1. API service availability during the kill event remains at 100% via the surviving replica.
  2. Kubernetes Deployment controller detects pod termination within < 2 seconds.
  3. Replacement pod starts and passes readiness probe (`/ready`) within < 15 seconds.
  4. Mean Time to Detect (MTTD) < 5s; Mean Time to Recover (MTTR) < 20s.

---

### CE-02: API-to-Database Network Latency & Timeout Degradation

- **Objective**: Validate system behavior and observability alerting when network communication between the API layer and the PostgreSQL database experiences severe latency.
- **Manifest**: `chaos/experiments/ce02-network-latency.yaml`
- **Fault Type**: Chaos Mesh `NetworkChaos`
- **Action**: `delay`
- **Parameters**:
  - Latency: `300ms`
  - Jitter: `50ms`
  - Correlation: `25%`
  - Direction: `to`
  - Target: Pods with `app: postgres` in `resiliencelab`
- **Duration**: `60s`
- **Hypothesis**:
  1. API p95 response time for order creation increases by ~300ms.
  2. Requests still succeed without connection termination.
  3. Prometheus evaluates p95 latency and triggers `HighRequestLatency` alert.
  4. When the 60s duration expires, latency immediately drops back to baseline (< 10ms).

---

### CE-03: PostgreSQL Dependency Outage & Graceful Degradation

- **Objective**: Validate application fault tolerance, readiness probe decoupling, alert firing, webhook incident logging, and data persistence on PVC during total database failure.
- **Fault Type**: Controlled scale-down / Network Isolation
- **Action**: Scale PostgreSQL deployment to 0 replicas for 45s, then restore to 1 replica.
- **Hypothesis**:
  1. Liveness `/health` remains 200 OK (process healthy).
  2. Readiness `/ready` returns HTTP 503 Service Unavailable (dependency down).
  3. Prometheus fires `PostgresDown` and `DatabaseConnectionFailures` alerts.
  4. Alertmanager dispatches alert payload to Python Webhook Receiver.
  5. Upon database restoration, API automatically reconnects via connection pool without pod restarts.
  6. Orders previously stored on PVC remain intact with 0 data loss.

---

### CE-04: API CPU Saturation & Autoscaling Response

- **Objective**: Validate system telemetry under resource starvation and observe CPU metric elevation and autoscaling dynamics.
- **Manifest**: `chaos/experiments/ce04-cpu-stress.yaml`
- **Fault Type**: Chaos Mesh `StressChaos`
- **Action**: CPU stress injection
- **Parameters**:
  - Workers: 1
  - Load: 85%
  - Duration: `60s`
- **Selector**: 1 API pod in `resiliencelab` with `chaos-enabled: "true"`
- **Hypothesis**:
  1. Targeted pod CPU utilization elevates to >= 85%.
  2. Prometheus and metrics-server capture the spike within 1 scrape interval (15s).
  3. Unaffected replica handles excess traffic.
  4. At the end of the 60s window, stress terminates and CPU metrics stabilize to idle baseline (< 5%).
