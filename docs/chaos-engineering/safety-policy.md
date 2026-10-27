# ResilienceLab — Chaos Engineering Safety Policy & Guardrails

## 1. Principles of Chaos Engineering Safety

Chaos engineering in ResilienceLab is conducted under strict safety controls. Although our environment is a simulated production system running in Kubernetes, we enforce real-world SRE safety policies to prevent uncontrolled degradation, resource exhaustion, or cascading failures across platform infrastructure.

---

## 2. Blast Radius Controls

### 2.1 Namespace Isolation
- **Allowed Target Namespace**: `resiliencelab` ONLY.
- **Strictly Prohibited Namespaces**:
  - `kube-system` (Kubernetes control plane components, CoreDNS, kube-proxy)
  - `monitoring` (Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics)
  - `chaos-mesh` (Chaos controller-manager, chaos-daemon)
  - `default`

Any experiment definition attempting to target a prohibited namespace is rejected automatically by the experiment controller and validation scripts.

### 2.2 Workload Opt-In Labeling
Fault injection is strictly opt-in. A workload is eligible for injection **if and only if** its metadata contains:
```yaml
chaos-enabled: "true"
```
Workloads lacking this label (such as `webhook-receiver` or ad-hoc debugging pods) will NEVER be targeted.

### 2.3 Replica Availability Constraints
- For multi-replica workloads (e.g., `resiliencelab-api` with 2 replicas), experiments MUST NOT affect more than **1 replica** (`value: "1"`, `mode: one`).
- At least one surviving replica must remain fully functional and able to serve production traffic during the experiment window.
- The Kubernetes Service endpoint must maintain continuous availability with 0% unhandled connection drops.

### 2.4 Duration & Concurrency Limits
- **Maximum Experiment Duration**: 60 seconds.
- **Experiment Concurrency**: Strictly **1**. No concurrent, overlapping, or pipelined experiments are permitted.
- **Cooldown Window**: A mandatory steady-state stabilization period of at least 30 seconds must follow every experiment before any subsequent test may begin.

---

## 3. Emergency Abort & Rollback (Kill Switch)

If an experiment exceeds acceptable error budgets, causes cluster instability, or hangs, the emergency abort procedure must be executed immediately.

### Fast Emergency Abort Command:
```bash
make chaos-abort
```

### Manual Kill Switch Commands:
```bash
# 1. Delete all active Chaos Mesh fault injections immediately
kubectl delete podchaos,networkchaos,stresschaos,iochaos,httpchaos,dnschaos --all -n resiliencelab --timeout=15s

# 2. In case of finalizer hang, strip finalizers
kubectl get podchaos,networkchaos,stresschaos -n resiliencelab -o json | \
  jq '.items[] | .metadata.finalizers = []' | kubectl apply -f -

# 3. Ensure API deployment has all replicas ready
kubectl scale deployment api -n resiliencelab --replicas=2
kubectl scale deployment postgres -n resiliencelab --replicas=1
kubectl scale deployment worker -n resiliencelab --replicas=1

# 4. Verify steady-state health
curl -s http://localhost:8000/ready
```

---

## 4. Pre-Flight & Post-Flight Verification Checklist

Before starting any experiment:
1. `resiliencelab` namespace has all deployments `100% READY`.
2. `/ready` endpoint returns HTTP 200 with database check healthy.
3. Prometheus scraping targets are in state `UP`.
4. No unresolved alerts are firing in Alertmanager.
5. Cluster memory headroom is verified (> 500 MiB free).

After any experiment:
1. Chaos resource is completely deleted (`kubectl get podchaos,networkchaos,stresschaos -n resiliencelab` returns `No resources found`).
2. All target pods have returned to healthy `Running` state with 0 crash loops.
3. API readiness probe `/ready` returns HTTP 200.
4. System metrics (p95 latency, error rate, CPU) have returned to baseline levels.
