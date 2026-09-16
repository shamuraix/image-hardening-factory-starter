#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
output_dir="${work_dir}/evidence/copacetic"
mkdir -p "${output_dir}"

status="skipped"
reason=""

if [[ -z "${FACTORY_COPA_COMMAND:-}" ]]; then
  reason="FACTORY_COPA_COMMAND is not configured; set it to run copacetic planning"
else
  export FACTORY_COPA_IMAGE_ARCHIVE="${work_dir}/image.oci.tar"
  export FACTORY_COPA_SBOM="${work_dir}/evidence/sbom.cdx.json"
  export FACTORY_COPA_FINDINGS="${work_dir}/evidence/findings.json"
  export FACTORY_COPA_OUTPUT_DIR="${output_dir}"
  [[ -s "${FACTORY_COPA_FINDINGS}" ]] || { echo "Copacetic requires scan findings" >&2; exit 1; }
  if bash -o pipefail -c "${FACTORY_COPA_COMMAND}" \
    >"${output_dir}/command.log" 2>&1; then
    status="completed"
    reason="copacetic command completed"
  else
    status="failed"
    reason="copacetic command failed; inspect evidence/copacetic/command.log"
  fi
fi

jq -n \
  --arg image "${FACTORY_IMAGE:-unknown}" \
  --arg catalog "${catalog}" \
  --arg status "${status}" \
  --arg reason "${reason}" \
  --arg command "${FACTORY_COPA_COMMAND:-}" \
  '{image:$image,catalog:$catalog,status:$status,reason:$reason,command:$command}' \
  >"${output_dir}/status.json"

if [[ "${status}" == "failed" ]]; then
  exit 1
fi
