#!/usr/bin/env bash
# Stage the evidence of each image's current commercial release for the
# exception-steward agent (internal-read trust class): blocking findings and an
# SBOM component list, retrieved and verified via the release request pins.
set -euo pipefail

: "${FACTORY_AGENT_ROOT:?}"
: "${ARTIFACTORY_READ_TOKEN:?}"
environment=${FACTORY_STEWARD_ENVIRONMENT:-commercial}
context="${FACTORY_AGENT_ROOT}/context/evidence"
mkdir -p "${context}"
shopt -s nullglob
for request in releases/"${environment}"/*.yaml; do
  image=$(basename "${request}" .yaml)
  scratch=$(mktemp -d)
  python3 -c 'import json,sys,yaml; json.dump(yaml.safe_load(open(sys.argv[1])), sys.stdout)' \
    "${request}" >"${scratch}/request.json"
  if ! scripts/fetch_evidence_bundle.sh "${scratch}/request.json" "${scratch}/work" >"${scratch}/fetch.log" 2>&1; then
    echo "${image}: evidence unavailable ($(tail -n 1 "${scratch}/fetch.log"))" >&2
    rm -rf "${scratch}"
    continue
  fi
  out="${context}/${image}"
  mkdir -p "${out}"
  jq '[.findings[] | {id, component, installedVersion, severity, fixAvailable, knownExploited,
        inApplicationArchive}]' "${scratch}/work/evidence/findings.json" >"${out}/findings.json"
  jq '[.components[]? | {name, version, purl}]' "${scratch}/work/evidence/sbom.cdx.json" \
    >"${out}/sbom-components.json"
  cp "${scratch}/work/evidence/gate-result.json" "${out}/gate-result.json"
  jq -n --arg request "${request}" --arg digest "$(jq -r '.candidate.digest' "${scratch}/request.json")" \
    '{request:$request,digest:$digest}' >"${out}/release.json"
  rm -rf "${scratch}"
  echo "${image}: staged release evidence"
done
