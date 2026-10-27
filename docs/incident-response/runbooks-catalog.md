# ResilienceLab — Declarative Runbooks Catalog & Operator Guide

## 1. Overview

ResilienceLab utilizes declarative YAML runbooks located in `automation/runbooks/`. Each runbook specifies preconditions, matching alert rules, target services, risk levels, and ordered remediation actions.

---

## 2. Runbook Catalog

| Runbook ID | Target Service | Matching Alerts | Actions | Risk Level | Human Approval Required? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`api-unavailable`** | `api` | `APIHighErrorRate`, `APISlowResponses`, `APIPodFailure` | 1. `verify_self_healing`<br>2. `restart_deployment`<br>3. `scale_deployment` | LOW / MEDIUM | ❌ Auto-Approved |
| **`database-unavailable`** | `postgres` | `DatabaseConnectionFailures`, `PostgresDown` | 1. `verify_pvc_integrity`<br>2. `scale_deployment` (replicas=1) | LOW / **HIGH** | **✅ Mandatory Approval** |
| **`worker-failure`** | `worker` | `WorkerProcessingFailures`, `WorkerDown` | 1. `restart_deployment` (`deploy/worker`) | LOW | ❌ Auto-Approved |
| **`high-cpu`** | `api` | `HighCPUUtilization`, `CPUSaturation` | 1. `scale_deployment` (replicas=3) | MEDIUM | ❌ Auto-Approved |
| **`high-latency`** | `api` | `HighRequestLatency`, `LatencySpike` | 1. `verify_self_healing`<br>2. `check_chaos_mesh` | LOW | ❌ Auto-Approved |
| **`pod-crash-loop`** | `api`, `worker` | `PodCrashLooping`, `ContainerRestarting` | 1. `restart_deployment` | LOW | ❌ Auto-Approved |

---

## 3. Human Approval Workflow

When a runbook contains an action marked with `risk_level: HIGH` or `requires_approval: true` (such as restoring or scaling the stateful database tier), the engine stages the action and halts automatic execution.

### Approval Step-by-Step:
1. **Approval Request Created**:
   - The engine generates an `ApprovalRequest` record with ID `APP-XXXXXX` and a 5-minute expiration timer (`expires_at`).
   - The incident timeline records `APPROVAL_REQUESTED`.
2. **Operator Inspection**:
   - The operator inspects the pending approval:
     ```bash
     curl -s http://localhost:5001/api/incidents/<incident_id> | jq .remediations
     ```
3. **Operator Decision**:
   - **To Approve**:
     ```bash
     curl -X POST http://localhost:5001/api/incidents/<incident_id>/approve-remediation \
       -H "Content-Type: application/json" \
       -d '{
         "approval_id": "APP-XXXXXX",
         "decision": "APPROVED",
         "approver": "senior_sre",
         "reason": "Database PVC verified healthy; approving restore"
       }'
     ```
   - **To Reject**:
     ```bash
     curl -X POST http://localhost:5001/api/incidents/<incident_id>/approve-remediation \
       -H "Content-Type: application/json" \
       -d '{
         "approval_id": "APP-XXXXXX",
         "decision": "REJECTED",
         "approver": "senior_sre",
         "reason": "Scheduled maintenance in progress"
       }'
     ```
4. **Execution & State Transition**:
   - Upon approval, the engine executes the remediation, records `APPROVAL_GRANTED` and `REMEDIATION_EXECUTED` in the audit timeline, and transitions the incident into `VERIFYING`.
   - Upon rejection, the incident transitions to `ESCALATED`.
