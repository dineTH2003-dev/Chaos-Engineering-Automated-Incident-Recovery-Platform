# Postmortem: INC-PM-API-01 — API Service Spike

**Date:** 2026-10-27T14:20:18.513370 | **Severity:** `WARNING` | **Status:** `RESOLVED` | **Service:** `api`

## 1. Incident Overview
- **Incident ID:** `INC-PM-API-01`
- **Title:** API Service Spike
- **Affected Service:** `api` (Namespace: `resiliencelab`)
- **Source Alert:** `APIHighErrorRate`
- **Lifecycle Status:** `RESOLVED`

## 2. Executive Summary
On 2026-10-27, service `api` experienced an incident triggered by `APIHighErrorRate`. The incident lasted a total of **60.0s**, consuming **0.2315%** of the monthly error budget. The platform automated incident response system detected the alert, ran multi-source diagnosis with `HIGH` confidence, and guided remediation to restore service health.

## 3. Impact Assessment
- **User Impact:** All external API consumers during degraded window
- **Availability Drop:** ~2.0%
- **Error Budget Consumed:** `0.2315%` of 30-day budget (12,960s allowed downtime)

## 4. SRE Key Metrics Duration Table
| Metric | Duration (s) | Description |
| :--- | :--- | :--- |
| **TTD** (Time to Detect) | 0.0s | Alertmanager detection & webhook delivery latency |
| **TTM** (Time to Mitigate) | 45.0s | Runbook execution / operator approval duration |
| **TTR** (Time to Recover) | 15.0s | Verification window until consecutive probes passed |
| **Total Outage Duration** | **60.0s** | Total elapsed time from start to verified recovery |

## 5. Chronological Incident Timeline
| Relative Time | Event Type | Actor | Summary |
| :--- | :--- | :--- | :--- |

## 6. Root Cause Analysis (5 Whys)
**Suspected Cause:** Automated diagnosis determined workload failure
**Diagnostic Confidence:** `HIGH`

- Why 1: Service 'api' stopped responding to requests.
- Why 2: Underlying pods failed health check or database connection.
- Why 3: Dependency became unreachable or resource limits saturated.
- Why 4: Fault injection or infrastructure degradation triggered failure.
- Why 5: Self-healing required runbook intervention or replica restoration.

## 7. Contributing Factors
- Infrastructure state on service `api`.
- Workload pressure and external dependency coupling.

## 8. Detection Analysis
- Fired alert: `APIHighErrorRate`.
- Deduplication: subsequent notifications cleanly aggregated into existing incident record.

## 9. Remediation & Action Item Record
| Remediation ID | Action Type | Target | Risk | Approval | Dry-Run | State |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |

## 10. Recovery Verification Evidence
- Multi-step verification executed: Kubernetes pod readiness confirmed.
- Consecutive `/health` and `/ready` HTTP probe window succeeded without errors.
- Prometheus error rate returned below 1.0% threshold.

## 11. What Went Well
- Automated diagnosis quickly correlated failure indicators.
- Human approval gate safeguarded high-risk state changes.
- Independent verification prevented premature incident closure.

## 12. What Went Wrong
- Initial failure degraded client traffic before intervention completed.

## 13. Where We Got Lucky
- Persistent volumes retained all application data without corruption.

## 14. Preventive Action Items & Ownership
| Action Item | Type | Priority | Owner |
| :--- | :--- | :--- | :--- |
| Introduce automated health watchdog for api | Prevent | P1 | SRE Platform Team |
| Tune HPA scaling responsiveness and readiness probe thresholds | Mitigate | P2 | Cloud Infrastructure Team |

## 15. Sign-off & Audit Trail
- **Author:** ResilienceLab SRE Autonomous Agent v1.0.0
- **Generated At:** 2026-10-27T14:21:18.546781+00:00
- **Review Status:** Signed off by Automated SRE Supervisor
