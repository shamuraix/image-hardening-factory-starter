#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
output="${work_dir}/evidence/scans/fcs"
mkdir -p "${output}"

: "${FALCON_CLIENT_ID:?FALCON_CLIENT_ID is required}"
: "${FALCON_CLIENT_SECRET:?FALCON_CLIENT_SECRET is required}"
: "${FALCON_REGION:?FALCON_REGION is required}"
case "${FALCON_REGION}" in
  us-1|us-2|eu-1|us-gov-1|us-gov-2) ;;
  *) echo "unsupported FALCON_REGION: ${FALCON_REGION}" >&2; exit 2 ;;
esac
command -v fcs >/dev/null || { echo "fcs CLI is not installed" >&2; exit 2; }
scripts/require_rootless.sh podman

platform=$(yq -r '.build.platforms[0]' "${catalog}")
# shellcheck disable=SC1090,SC1091
source "${work_dir}/build.env"
: "${LOCAL_IMAGE_REF:?LOCAL_IMAGE_REF is missing from build.env}"
: "${IMAGE_DIGEST:?IMAGE_DIGEST is missing from build.env}"
podman load -i "${work_dir}/image.oci.tar" >"${output}/load.log"
podman image exists "${LOCAL_IMAGE_REF}"

socket_dir=$(mktemp -d)
socket="${socket_dir}/podman.sock"
podman system service --time=0 "unix://${socket}" >"${output}/podman-service.log" 2>&1 &
service_pid=$!
# shellcheck disable=SC2317
cleanup() {
  kill "${service_pid}" >/dev/null 2>&1 || true
  wait "${service_pid}" >/dev/null 2>&1 || true
  rm -rf "${socket_dir}"
}
trap cleanup EXIT
for _ in $(seq 1 50); do
  [[ -S ${socket} ]] && break
  sleep 0.1
done
[[ -S ${socket} ]] || { echo "Podman API socket did not start" >&2; exit 2; }

export FCS_CLIENT_ID="${FALCON_CLIENT_ID}"
export FCS_CLIENT_SECRET="${FALCON_CLIENT_SECRET}"
assessment="${output}/assessment.json"
sbom="${output}/sbom.cdx.json"

set +e
fcs scan image "${LOCAL_IMAGE_REF}" \
  --falcon-region "${FALCON_REGION}" \
  --socket "unix://${socket}" \
  --platform "${platform}" \
  --strict-digest \
  --format json \
  --output "${assessment}" \
  --no-color >"${output}/assessment.log" 2>&1
assessment_exit=$?
set -e

# CrowdStrike publicly supports CycloneDX JSON, not SPDX, for its image SBOM.
# Syft remains the factory's SPDX producer.
set +e
fcs scan image "${LOCAL_IMAGE_REF}" \
  --falcon-region "${FALCON_REGION}" \
  --socket "unix://${socket}" \
  --platform "${platform}" \
  --strict-digest \
  --sbom-only \
  --format cyclonedx-json \
  --output "${sbom}" \
  --no-color >"${output}/sbom.log" 2>&1
sbom_exit=$?
set -e

report_valid=false
sbom_valid=false
jq -e 'type == "object"' "${assessment}" >/dev/null 2>&1 && report_valid=true
jq -e '.bomFormat == "CycloneDX"' "${sbom}" >/dev/null 2>&1 && sbom_valid=true
version=$(fcs version 2>&1 | head -n1 || fcs --version 2>&1 | head -n1 || true)
assessment_passed=false
if [[ ${assessment_exit} -eq 0 && ${report_valid} == true && ${sbom_exit} -eq 0 && ${sbom_valid} == true ]]; then
  assessment_passed=true
fi

jq -n \
  --arg assessedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg image "${LOCAL_IMAGE_REF}" \
  --arg digest "${IMAGE_DIGEST}" \
  --arg platform "${platform}" \
  --arg region "${FALCON_REGION}" \
  --arg version "${version}" \
  --argjson exitCode "${assessment_exit}" \
  --argjson sbomExitCode "${sbom_exit}" \
  --argjson reportValid "${report_valid}" \
  --argjson sbomValid "${sbom_valid}" \
  --argjson assessmentPassed "${assessment_passed}" \
  '{schemaVersion:"1.0",scanner:"crowdstrike-fcs",assessedAt:$assessedAt,
    image:$image,digest:$digest,platform:$platform,region:$region,version:$version,
    exitCode:$exitCode,sbomExitCode:$sbomExitCode,reportValid:$reportValid,sbomValid:$sbomValid,
    assessmentPassed:$assessmentPassed}' >"${output}/status.json"

# Always hand a structured result to the policy job. The policy gate, rather
# than GitLab job scheduling, is the authoritative fail-closed decision point.
unset FCS_CLIENT_ID FCS_CLIENT_SECRET FALCON_CLIENT_SECRET
exit 0
