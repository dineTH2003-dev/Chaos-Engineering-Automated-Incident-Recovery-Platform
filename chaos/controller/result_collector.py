"""
ResilienceLab — Experiment Result Collector & Report Generator
Formats structured JSON and Markdown post-mortem reports for chaos experiments.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict


class ResultCollector:
    """Aggregates telemetry, computes SLO metrics, and outputs structured reports."""

    def __init__(self, experiment_id: str, experiment_name: str, hypothesis: str):
        self.experiment_id = experiment_id
        self.experiment_name = experiment_name
        self.hypothesis = hypothesis
        self.start_time: str = ""
        self.end_time: str = ""
        self.duration_seconds: float = 0.0
        self.mttd_seconds: float = 0.0
        self.mttr_seconds: float = 0.0
        self.steady_state_baseline: Dict[str, Any] = {}
        self.chaos_telemetry: Dict[str, Any] = {}
        self.recovery_telemetry: Dict[str, Any] = {}
        self.prober_summary: Dict[str, Any] = {}
        self.alerts_observed: list = []
        self.slo_target_availability_pct: float = 99.0
        self.slo_passed: bool = False
        self.verdict: str = "PENDING"
        self.notes: list = []

    def finalize(
        self,
        start_time: datetime,
        end_time: datetime,
        mttd: float,
        mttr: float,
        prober_summary: Dict[str, Any],
        alerts: list,
        notes: list,
    ):
        """Finalize results and evaluate SLO criteria."""
        self.start_time = start_time.isoformat()
        self.end_time = end_time.isoformat()
        self.duration_seconds = round((end_time - start_time).total_seconds(), 2)
        self.mttd_seconds = round(mttd, 2)
        self.mttr_seconds = round(mttr, 2)
        self.prober_summary = prober_summary
        self.alerts_observed = alerts
        self.notes = notes

        avail = prober_summary.get("availability_pct", 100.0)
        self.slo_passed = (avail >= self.slo_target_availability_pct)
        self.verdict = "PASS" if self.slo_passed else "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        """Convert complete experiment result to dictionary."""
        return {
            "experiment_id": self.experiment_id,
            "experiment_name": self.experiment_name,
            "hypothesis": self.hypothesis,
            "execution": {
                "start_time": self.start_time,
                "end_time": self.end_time,
                "duration_seconds": self.duration_seconds,
            },
            "sre_metrics": {
                "mttd_seconds": self.mttd_seconds,
                "mttr_seconds": self.mttr_seconds,
                "availability_pct": self.prober_summary.get("availability_pct", 100.0),
                "slo_target_availability_pct": self.slo_target_availability_pct,
                "slo_met": self.slo_passed,
                "verdict": self.verdict,
            },
            "probing_summary": self.prober_summary,
            "baseline": self.steady_state_baseline,
            "chaos_telemetry": self.chaos_telemetry,
            "recovery_telemetry": self.recovery_telemetry,
            "alerts_observed": self.alerts_observed,
            "notes": self.notes,
        }

    def save_json_report(self, base_dir: str = "reports/experiments") -> str:
        """Save structured JSON report file."""
        os.makedirs(base_dir, exist_ok=True)
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{self.experiment_id.lower()}_{timestamp_str}.json"
        filepath = os.path.join(base_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

        return filepath

    def save_markdown_report(self, base_dir: str = "reports/incidents") -> str:
        """Save human-readable Markdown post-mortem / incident report."""
        os.makedirs(base_dir, exist_ok=True)
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"INCIDENT_{self.experiment_id.upper()}_{timestamp_str}.md"
        filepath = os.path.join(base_dir, filename)

        avail = self.prober_summary.get("availability_pct", 100.0)
        p95 = self.prober_summary.get("p95_latency_ms", 0.0)

        md = f"""# SRE Incident Post-Mortem: {self.experiment_id} — {self.experiment_name}

**Status**: {self.verdict}  
**Date**: {self.start_time}  
**Author**: SRE Team / Automated Chaos Controller  
**Experiment ID**: `{self.experiment_id}`  

---

## 1. Executive Summary

During automated resilience testing, fault injection was performed to validate system behavior under failure conditions.
- **Hypothesis**: {self.hypothesis}
- **Availability Achieved**: `{avail}%` (SLO Target: `>= {self.slo_target_availability_pct}%`)
- **Mean Time to Detect (MTTD)**: `{self.mttd_seconds}s`
- **Mean Time to Recover (MTTR)**: `{self.mttr_seconds}s`
- **SLO Status**: `{"✅ MET" if self.slo_passed else "❌ BREACHED"}`

---

## 2. Telemetry & Impact Analysis

| Metric | Baseline | During Chaos | Post-Recovery |
| :--- | :--- | :--- | :--- |
| **Availability** | `{self.steady_state_baseline.get('availability_pct', 100.0)}%` | `{avail}%` | `{self.recovery_telemetry.get('availability_pct', 100.0)}%` |
| **p95 Latency** | `{self.steady_state_baseline.get('p95_latency_ms', 0.0)} ms` | `{p95} ms` | `{self.recovery_telemetry.get('p95_latency_ms', 0.0)} ms` |
| **Ready Replicas** | `{self.steady_state_baseline.get('ready_replicas', 2)}` | `{self.chaos_telemetry.get('ready_replicas', '-')}` | `{self.recovery_telemetry.get('ready_replicas', 2)}` |
| **Error Rate** | `{self.steady_state_baseline.get('error_rate_pct', 0.0)}%` | `{self.chaos_telemetry.get('error_rate_pct', 0.0)}%` | `{self.recovery_telemetry.get('error_rate_pct', 0.0)}%` |

---

## 3. Alerts Observed

"""
        if self.alerts_observed:
            for alert in self.alerts_observed:
                md += f"- **{alert.get('alertname')}** (State: `{alert.get('alertstate')}`, Severity: `{alert.get('severity')}`, Pod: `{alert.get('pod', 'N/A')}`)\n"
        else:
            md += "- No critical alerts triggered during this test window.\n"

        md += f"""
---

## 4. Key Observations & Lessons Learned

"""
        for note in self.notes:
            md += f"- {note}\n"

        md += f"""
---
*Report generated automatically by ResilienceLab Chaos Experiment Controller.*
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)

        return filepath
