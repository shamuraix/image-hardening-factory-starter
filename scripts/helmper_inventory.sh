#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
output_dir="${work_dir}/evidence/helmper"
mkdir -p "${output_dir}"

status="skipped"
reason=""

mapfile -t charts < <(find "${work_dir}/context" -type f -name Chart.yaml -print | sort)
if [[ ${#charts[@]} -eq 0 ]]; then
  reason="no helm charts were found in the build context"
elif [[ -z "${FACTORY_HELMPER_COMMAND:-}" ]]; then
  reason="FACTORY_HELMPER_COMMAND is not configured; set it to run helmper"
else
  if bash -o pipefail -c "${FACTORY_HELMPER_COMMAND}" \
    >"${output_dir}/command.log" 2>&1; then
    status="completed"
    reason="helmper command completed"
  else
    status="failed"
    reason="helmper command failed; inspect evidence/helmper/command.log"
  fi
fi

printf '%s\n' "${charts[@]}" >"${output_dir}/charts.txt"
jq -n \
  --arg image "${FACTORY_IMAGE:-unknown}" \
  --arg catalog "${catalog}" \
  --arg status "${status}" \
  --arg reason "${reason}" \
  --arg command "${FACTORY_HELMPER_COMMAND:-}" \
  --argjson charts "$(printf '%s\n' "${charts[@]}" | jq -R -s 'split("\n")[:-1]')" \
  '{image:$image,catalog:$catalog,status:$status,reason:$reason,command:$command,charts:$charts}' \
  >"${output_dir}/status.json"

if [[ "${status}" == "failed" ]]; then
  exit 1
fi
