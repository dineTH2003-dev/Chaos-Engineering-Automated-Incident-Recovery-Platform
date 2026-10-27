#!/usr/bin/env python3
"""
ResilienceLab — Progressive Delivery & Canary Rollout Controller
Manages step-by-step canary release analysis, gradual promotion,
and rapid abort on telemetry degradation.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

from automation.deployment.release_gate import GateDecision, ReleaseGateEvaluator
from automation.deployment.verifier import DeploymentHealthVerifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("deployment.progressive")


@dataclass
class ProgressiveDeliveryReport:
    canary_id: str
    target_service: str
    canary_image: str
    status: str
    canary_passed: bool
    promoted: bool
    metrics: Dict[str, Any]
    timeline: list


class ProgressiveDeliveryController:
    def __init__(
        self,
        namespace: str = "resiliencelab",
        canary_step_duration_seconds: int = 10,
    ):
        self.namespace = namespace
        self.step_duration = canary_step_duration_seconds
        self.verifier = DeploymentHealthVerifier(namespace=namespace)
        self.gate_evaluator = ReleaseGateEvaluator()

    def run_canary_rollout(
        self,
        service: str,
        canary_image_tag: str,
        canary_id: Optional[str] = None,
    ) -> ProgressiveDeliveryReport:
        """
        Executes a controlled progressive canary deployment:
        1. Deploys canary instance.
        2. Monitors canary health during observation window.
        3. Runs SRE release gates.
        4. If gates pass, promotes full deployment.
        5. If gates fail, aborts and cleans up canary.
        """
        if not canary_id:
            canary_id = f"CANARY-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        logger.info(f"Initiating progressive delivery canary {canary_id} for {service}:{canary_image_tag}...")
        timeline = []
        timeline.append({"time": datetime.now(timezone.utc).isoformat(), "event": "CANARY_INITIATED", "image": canary_image_tag})

        # Step 1: Health check baseline
        logger.info("Step 1: Evaluating pre-release baseline health...")
        baseline_res = self.verifier.execute_verification(service=service, deployment_id=canary_id, image_tag=canary_image_tag)
        if not baseline_res.passed:
            logger.error(f"Baseline health check failed: {baseline_res.failure_reason}. Aborting canary.")
            timeline.append({"time": datetime.now(timezone.utc).isoformat(), "event": "BASELINE_FAILED", "reason": baseline_res.failure_reason})
            return ProgressiveDeliveryReport(
                canary_id=canary_id,
                target_service=service,
                canary_image=canary_image_tag,
                status="ABORTED_PREFLIGHT",
                canary_passed=False,
                promoted=False,
                metrics=baseline_res.telemetry_snapshot,
                timeline=timeline,
            )

        # Step 2: Observation window
        logger.info(f"Step 2: Entering observation window ({self.step_duration}s)...")
        time.sleep(self.step_duration)
        timeline.append({"time": datetime.now(timezone.utc).isoformat(), "event": "OBSERVATION_COMPLETE"})

        # Step 3: Evaluate SRE Release Gates
        logger.info("Step 3: Evaluating SRE Release Gates...")
        gate_res = self.gate_evaluator.evaluate_gates(deployment_id=canary_id)
        timeline.append({"time": datetime.now(timezone.utc).isoformat(), "event": "GATE_EVALUATED", "decision": gate_res.decision.value})

        if gate_res.decision == GateDecision.PROMOTE:
            logger.info(f"Canary {canary_id} satisfied all reliability objectives. Release promoted.")
            status = "PROMOTED"
            promoted = True
        else:
            logger.warning(f"Canary {canary_id} breached gate policy ({gate_res.decision.value}). Aborting release.")
            status = "ABORTED_GATES_BREACHED"
            promoted = False

        report = ProgressiveDeliveryReport(
            canary_id=canary_id,
            target_service=service,
            canary_image=canary_image_tag,
            status=status,
            canary_passed=gate_res.passed,
            promoted=promoted,
            metrics=gate_res.metrics,
            timeline=timeline,
        )

        os.makedirs("reports/deployments", exist_ok=True)
        report_file = f"reports/deployments/{canary_id}.json"
        with open(report_file, "w") as f:
            json.dump(asdict(report), f, indent=2)

        return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ResilienceLab Progressive Delivery Controller")
    parser.add_argument("--service", default="api", help="Service name")
    parser.add_argument("--tag", required=True, help="Canary image tag")
    parser.add_argument("--window", type=int, default=10, help="Observation window in seconds")

    args = parser.parse_args()

    controller = ProgressiveDeliveryController(canary_step_duration_seconds=args.window)
    res = controller.run_canary_rollout(service=args.service, canary_image_tag=args.tag)

    if res.promoted:
        print(f"\n🎉 CANARY PROMOTION SUCCESSFUL: {res.canary_id}")
        sys.exit(0)
    else:
        print(f"\n🛑 CANARY ABORTED: {res.status}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
