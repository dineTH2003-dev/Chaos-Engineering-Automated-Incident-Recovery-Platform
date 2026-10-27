#!/usr/bin/env bash
set -euo pipefail

# ResilienceLab — Run Chaos Experiment Script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${SCRIPT_DIR}"

EXPERIMENT="${1:-ce01}"

echo "=========================================================="
echo "ResilienceLab Chaos Controller — Running: ${EXPERIMENT}"
echo "=========================================================="

export PYTHONPATH="${SCRIPT_DIR}"
python3 -m chaos.controller.experiment_runner --experiment "${EXPERIMENT}"
