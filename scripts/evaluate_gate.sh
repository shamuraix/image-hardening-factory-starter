#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
evidence="${work_dir}/evidence"

# Legacy scanners are informational. Preserve their evidence when available,
# but do not prevent the authoritative FCS assessment from reaching policy.
if ! jq -e 'type == "object" and (.findings | type == "array")' \
  "${evidence}/findings.json" >/dev/null 2>&1; then
  printf '{"findings":[],"informationalErrors":["legacy scanner evidence unavailable"]}\n' \
    >"${evidence}/findings.json"
fi
if ! jq -e 'type == "object"' "${evidence}/database-status.json" >/dev/null 2>&1; then
  jq -n '{available:false}' >"${evidence}/database-status.json"
fi

python3 -m factory.cli gate-input \
  --image "${FACTORY_IMAGE}" \
  --image-digest "$(jq -er '.digest' "${work_dir}/image-metadata.json")" \
  --sbom "${evidence}/sbom.cdx.json" \
  --findings "${evidence}/findings.json" \
  --compliance "${evidence}/compliance/result.json" \
  --tests "${evidence}/tests/result.json" \
  --database-status "${evidence}/database-status.json" \
  --fcs-status "${evidence}/scans/fcs/status.json" \
  --output "${evidence}/gate-input.json"

yq -o=json '.policy' "${catalog}" >"${work_dir}/policy.json"
jq --slurpfile policy "${work_dir}/policy.json" '. + {policy:$policy[0]}' \
  "${evidence}/gate-input.json" >"${evidence}/gate-input.tmp.json"
mv "${evidence}/gate-input.tmp.json" "${evidence}/gate-input.json"

opa eval --format=json \
  --data policies/rego \
  --data policies/exceptions/approved.json \
  --input "${evidence}/gate-input.json" \
  'data.factory.release.decision' \
  | jq -e '.result[0].expressions[0].value' >"${evidence}/gate-result.json"

allowed=$(jq -r '.allow' "${evidence}/gate-result.json")
printf 'FACTORY_GATE_ALLOWED=%s\n' "${allowed}" >"${work_dir}/gate.env"
if [[ "${allowed}" != true ]]; then
  jq -r '.deny[]' "${evidence}/gate-result.json" >&2
fi
