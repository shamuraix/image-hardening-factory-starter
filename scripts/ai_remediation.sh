#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
: "${AI_BASE_URL:?}"
: "${AI_MODEL:?}"
scripts/ai_remediation.py "${catalog}" "${work_dir}"
