# Flux CD Operations & Maintenance Guide

**Author**: Senior Site Reliability Engineer, DevOps & Cloud-Native Architect  
**Phase**: Phase 5 — Stage E  
**Status**: OPERATIONAL GUIDE  

---

## 1. Operational CLI Commands

### 1.1 Status & Health Inspection
```bash
# Verify all Flux controllers
flux check

# Inspect Git sources
flux get sources git -A

# Inspect Helm releases
flux get helmreleases -A

# Inspect Kustomizations
flux get kustomizations -A
```

### 1.2 On-Demand Reconciliation
When an urgent deployment or configuration change is merged, operators can trigger immediate reconciliation without waiting for the 1-minute polling interval:
```bash
# Reconcile Git repository source immediately
flux reconcile source git resiliencelab -n flux-system

# Reconcile Helm release
flux reconcile helmrelease resiliencelab -n resiliencelab
```

---

## 2. Makefile Operations

Pre-configured targets exist in the project [`Makefile`](file:///Makefile):

```bash
# Show comprehensive status of Flux controllers, sources, and releases
make gitops-status

# Trigger immediate manual reconciliation of Git sources and Helm releases
make gitops-reconcile
```

---

## 3. Drift Detection & Self-Healing Verification

To verify that Flux enforces the desired state and automatically corrects out-of-band mutations:

1. **Simulate Configuration Drift**:
   ```bash
   # Manually scale the API deployment out-of-band
   kubectl scale deployment api --replicas=4 -n resiliencelab
   ```
2. **Observe Self-Healing**:
   ```bash
   # Wait for reconciliation or trigger immediate sync
   make gitops-reconcile
   
   # Confirm replica count is restored to Git desired value (2 replicas)
   kubectl get deployment api -n resiliencelab
   ```

---

## 4. Emergency Procedures: Suspending Reconciliation

In high-severity incidents where operators must perform emergency troubleshooting without Flux undoing state mutations:

```bash
# Suspend reconciliation
flux suspend helmrelease resiliencelab -n resiliencelab

# Perform emergency remediation with kubectl...

# Once incident is resolved and desired state is committed to Git, resume:
flux resume helmrelease resiliencelab -n resiliencelab
```
