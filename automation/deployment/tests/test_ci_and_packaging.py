"""
ResilienceLab — Phase 5 Unit Tests: CI Pipeline, Helm Packaging, and Security Policies.
"""

import os
import subprocess
import yaml

from automation.deployment.gitops_updater import get_current_image_tags, update_gitops_image_tag


def test_ci_workflow_yaml_validity():
    """Validates that .github/workflows/ci.yaml parses cleanly as valid YAML."""
    ci_path = ".github/workflows/ci.yaml"
    assert os.path.exists(ci_path), "ci.yaml does not exist"
    with open(ci_path, "r") as f:
        data = yaml.safe_load(f)
    assert data["name"] == "CI Pipeline"
    triggers = data.get("on") or data.get(True) or {}
    assert "push" in triggers
    assert "pull_request" in triggers
    assert "jobs" in data
    assert "lint-and-syntax" in data["jobs"]
    assert "test-unit-and-integration" in data["jobs"]
    assert "container-build-and-scan" in data["jobs"]


def test_trivy_security_policy_file():
    """Validates that .trivyignore exists and has documented instructions."""
    ignore_path = ".trivyignore"
    assert os.path.exists(ignore_path), ".trivyignore must exist"
    with open(ignore_path, "r") as f:
        content = f.read()
    assert "ResilienceLab Trivy Security Policy" in content


def test_helm_chart_metadata():
    """Validates that deployments/helm/resiliencelab/Chart.yaml has required fields."""
    chart_path = "deployments/helm/resiliencelab/Chart.yaml"
    assert os.path.exists(chart_path), "Chart.yaml must exist"
    with open(chart_path, "r") as f:
        data = yaml.safe_load(f)
    assert data["name"] == "resiliencelab"
    assert data["version"] == "0.1.0"
    assert data["appVersion"] == "1.0.0"


def test_helm_template_renders_without_errors():
    """Executes helm template to verify all chart templates render valid Kubernetes YAML."""
    res = subprocess.run(
        ["helm", "template", "resiliencelab", "deployments/helm/resiliencelab"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"helm template failed: {res.stderr}"
    docs = list(yaml.safe_load_all(res.stdout))
    assert len(docs) >= 5, "Expected at least 5 rendered Kubernetes resources"

    kinds = [d["kind"] for d in docs if d and "kind" in d]
    assert "Deployment" in kinds
    assert "Service" in kinds
    assert "ConfigMap" in kinds


def test_gitops_updater_local_file(tmp_path):
    """Tests that update_gitops_image_tag updates image tags accurately in YAML."""
    test_manifest = tmp_path / "test_helmrelease.yaml"
    initial_content = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "spec": {
            "values": {
                "api": {"image": {"tag": "old-v1"}},
                "worker": {"image": {"tag": "old-v1"}},
            }
        },
    }
    with open(test_manifest, "w") as f:
        yaml.dump(initial_content, f)

    record = update_gitops_image_tag("api", "new-v2", manifest_path=str(test_manifest), actor="test-runner")
    assert record["service"] == "api"
    assert record["new_tag"] == "new-v2"
    assert record["previous_tags"]["api"] == "old-v1"

    tags = get_current_image_tags(str(test_manifest))
    assert tags["api"] == "new-v2"
    assert tags["worker"] == "old-v1"
