#!/usr/bin/env python3
"""
ResilienceLab — GitOps Configuration & Promotion Updater
Programmatically updates desired container image tags in GitOps manifests,
maintains deployment audit records, and triggers Flux reconciliation.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import uuid
import yaml

DEFAULT_HELMRELEASE = "deployments/gitops/apps/resiliencelab/helmrelease.yaml"
DEPLOYMENT_LOG_FILE = "reports/deployments/deployments.log"


def get_current_image_tags(manifest_path: str = DEFAULT_HELMRELEASE) -> dict:
    """Reads current image tags from HelmRelease or values file."""
    if not os.path.exists(manifest_path):
        return {}
    with open(manifest_path, "r") as f:
        data = yaml.safe_load(f) or {}

    values = data.get("spec", {}).get("values", {})
    return {
        "api": values.get("api", {}).get("image", {}).get("tag", "unknown"),
        "worker": values.get("worker", {}).get("image", {}).get("tag", "unknown"),
    }


def update_gitops_image_tag(
    service: str,
    new_tag: str,
    manifest_path: str = DEFAULT_HELMRELEASE,
    actor: str = "ci-bot",
) -> dict:
    """
    Updates the desired image tag for a service in the GitOps HelmRelease manifest.
    Returns deployment record dictionary.
    """
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, "r") as f:
        data = yaml.safe_load(f)

    values = data.setdefault("spec", {}).setdefault("values", {})
    previous_tags = {
        "api": values.get("api", {}).get("image", {}).get("tag", "unknown"),
        "worker": values.get("worker", {}).get("image", {}).get("tag", "unknown"),
    }

    updated_services = []
    if service in ("api", "all"):
        api_img = values.setdefault("api", {}).setdefault("image", {})
        api_img["tag"] = new_tag
        updated_services.append("api")

    if service in ("worker", "all"):
        worker_img = values.setdefault("worker", {}).setdefault("image", {})
        worker_img["tag"] = new_tag
        updated_services.append("worker")

    with open(manifest_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    deployment_id = f"DEP-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    record = {
        "deployment_id": deployment_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "manifest_path": manifest_path,
        "service": service,
        "updated_services": updated_services,
        "previous_tags": previous_tags,
        "new_tag": new_tag,
        "status": "CONFIG_UPDATED",
    }

    # Persist to audit log
    os.makedirs(os.path.dirname(DEPLOYMENT_LOG_FILE), exist_ok=True)
    with open(DEPLOYMENT_LOG_FILE, "a") as log_f:
        log_f.write(json.dumps(record) + "\n")

    return record


def trigger_flux_reconciliation(namespace: str = "resiliencelab", release_name: str = "resiliencelab") -> bool:
    """Invokes Flux CLI or kubectl to force immediate HelmRelease reconciliation."""
    cmd = ["flux", "reconcile", "helmrelease", release_name, "-n", namespace]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback to annotating via kubectl
        annotate_cmd = [
            "kubectl", "annotate", "helmrelease", release_name,
            "-n", namespace,
            f"reconcile.fluxcd.io/requestedAt={datetime.now(timezone.utc).isoformat()}",
            "--overwrite"
        ]
        res = subprocess.run(annotate_cmd, capture_output=True, text=True)
        return res.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="ResilienceLab GitOps Promotion Updater")
    parser.add_argument("--service", choices=["api", "worker", "all"], default="api", help="Service to update")
    parser.add_argument("--tag", required=True, help="New immutable image tag")
    parser.add_argument("--manifest", default=DEFAULT_HELMRELEASE, help="Path to HelmRelease manifest")
    parser.add_argument("--actor", default="sre-operator", help="User or bot executing promotion")
    parser.add_argument("--reconcile", action="store_true", help="Trigger immediate Flux reconciliation")

    args = parser.parse_args()

    print(f"Updating {args.service} to tag '{args.tag}' in {args.manifest}...")
    rec = update_gitops_image_tag(args.service, args.tag, args.manifest, args.actor)
    print(f"Deployment record generated: {rec['deployment_id']}")

    if args.reconcile:
        print("Triggering Flux reconciliation...")
        if trigger_flux_reconciliation():
            print("Flux reconciliation triggered successfully.")
        else:
            print("Failed to trigger Flux reconciliation.", file=sys.stderr)


if __name__ == "__main__":
    main()
