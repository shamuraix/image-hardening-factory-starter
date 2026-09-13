#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
output_dir="${work_dir}/evidence/hummingbird"
mkdir -p "${output_dir}"

status="completed"
reason="generated hummingbird reproducibility summary"

gate_allowed=$(jq -r '.allow // false' "${work_dir}/evidence/gate-result.json")
fcs_assessment=$(jq -r '.assessmentPassed // false' "${work_dir}/evidence/scans/fcs/status.json")
provenance_sha256=""
if [[ -s "${work_dir}/evidence/provenance.json" ]]; then
  provenance_sha256=$(sha256sum "${work_dir}/evidence/provenance.json" | awk '{print $1}')
fi

if [[ -n "${FACTORY_HUMMINGBIRD_COMMAND:-}" ]]; then
  export FACTORY_HUMMINGBIRD_WORK_DIR="${work_dir}"
  if ! bash -o pipefail -c "${FACTORY_HUMMINGBIRD_COMMAND}" \
    >"${output_dir}/command.log" 2>&1; then
    status="failed"
    reason="FACTORY_HUMMINGBIRD_COMMAND failed; inspect evidence/hummingbird/command.log"
  fi
fi

jq -n \
  --arg image "${FACTORY_IMAGE:-unknown}" \
  --arg catalog "${catalog}" \
  --arg digest "$(jq -r '.digest' "${work_dir}/image-metadata.json")" \
  --arg sourceUrl "${FACTORY_SOURCE_URL:-}" \
  --arg commit "${FACTORY_COMMIT_SHA:-}" \
  --arg gateAllowed "${gate_allowed}" \
  --arg fcsAssessmentPassed "${fcs_assessment}" \
  --arg provenanceSha256 "${provenance_sha256}" \
  --arg sbomSha256 "$(sha256sum "${work_dir}/evidence/sbom.cdx.json" | awk '{print $1}')" \
  --arg status "${status}" \
  --arg reason "${reason}" \
  --arg command "${FACTORY_HUMMINGBIRD_COMMAND:-}" \
  '{
    image:$image,
    catalog:$catalog,
    digest:$digest,
    source:{url:$sourceUrl,commit:$commit},
    verification:{gateAllowed:($gateAllowed=="true"),fcsAssessmentPassed:($fcsAssessmentPassed=="true")},
    artifacts:{provenanceSha256:$provenanceSha256,sbomSha256:$sbomSha256},
    status:$status,
    reason:$reason,
    command:$command
  }' \
  >"${output_dir}/status.json"

if [[ "${status}" == "failed" ]]; then
  exit 1
fi
