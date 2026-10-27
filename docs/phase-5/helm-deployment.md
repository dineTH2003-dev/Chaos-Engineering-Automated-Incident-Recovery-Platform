# Helm-Based Deployment Packaging

**Author**: Senior Site Reliability Engineer, DevOps & Cloud-Native Architect  
**Phase**: Phase 5 — Stage D  
**Status**: IMPLEMENTED & VALIDATED  

---

## 1. Principles & Objectives

In Phase 5, the raw static Kubernetes manifests in `kubernetes/` are packaged into a modular, production-ready **Helm 3** chart located at [`deployments/helm/resiliencelab/`](file:///deployments/helm/resiliencelab/).

Key architectural principles:
1. **Parameterization**: Environment configurations (CPU/RAM requests, replica counts, log levels) are extracted into [`values.yaml`](file:///deployments/helm/resiliencelab/values.yaml).
2. **Environment Overlays**: Environment differences are decoupled into distinct values files:
   - `deployments/helm/environments/development/values.yaml` (Minikube local)
   - `deployments/helm/environments/staging/values.yaml` (Staging / GHCR references)
3. **Stateful Data Safety**: PersistentVolumeClaim `postgres-pvc` contains the annotation `"helm.sh/resource-policy": keep` to prevent accidental deletion during chart upgrades or uninstalls, guaranteeing that the **1,468 completed orders** remain protected.
4. **Chaos Testing Continuity**: Workload templates maintain `chaos-enabled: "true"` labels to ensure full compatibility with Phase 3 Chaos Mesh experiments (`PodChaos`, `NetworkChaos`, `StressChaos`).
5. **Observability Alignment**: Prometheus Operator `ServiceMonitor` resources are included as chart templates, maintaining continuous scraping by Prometheus v3.11.2.

---

## 2. Helm Chart Structure

```
deployments/helm/
├── environments/
│   ├── development/
│   │   └── values.yaml           # Minikube local dev overlay
│   └── staging/
│       └── values.yaml           # Staging environment overlay
└── resiliencelab/
    ├── Chart.yaml                # Chart metadata (v0.1.0 / appVersion 1.0.0)
    ├── values.yaml               # Default base configuration
    └── templates/
        ├── _helpers.tpl          # Standardized labeling & naming helpers
        ├── api-deployment.yaml   # API Deployment with RollingUpdate
        ├── api-service.yaml      # API ClusterIP Service (:80 -> :8000)
        ├── api-hpa.yaml          # HorizontalPodAutoscaler (2-4 pods)
        ├── worker-deployment.yaml# Background Worker Deployment
        ├── worker-service.yaml   # Worker Metrics Service (:8001)
        ├── postgres.yaml         # PostgreSQL Deployment, PVC, Service
        ├── configmap.yaml        # Application & database config
        ├── webhook-receiver.yaml # Incident Webhook Receiver
        └── servicemonitors.yaml  # ServiceMonitors for Prometheus scraping
```

---

## 3. Operational Usage

```bash
# Lint the chart for syntax and formatting errors
make helm-lint

# Render and inspect rendered manifests for development
make helm-template

# Dry-run install into Kubernetes
helm install resiliencelab deployments/helm/resiliencelab \
    -f deployments/helm/environments/development/values.yaml \
    --namespace resiliencelab \
    --dry-run
```
