#!/usr/bin/env bash
set -euo pipefail

: "${1:?catalog file is required}"
work_dir=${2:?work directory is required}
evidence="${work_dir}/evidence"
# shellcheck disable=SC1090,SC1091
source "${work_dir}/import.env"
: "${IMPORTED_IMAGE_REF:?}"
: "${IMPORTED_IMAGE_DIGEST:?}"
: "${COSIGN_KEY_PATH:?COSIGN_KEY_PATH must name the Jenkins file credential containing the encrypted Cosign private key}"
: "${COSIGN_PASSWORD:?COSIGN_PASSWORD is required to decrypt the Cosign private key}"
: "${ARTIFACTORY_REGISTRY:?}"
: "${ARTIFACTORY_SIGN_TOKEN:?ARTIFACTORY_SIGN_TOKEN must be a short-lived token with write access to quarantine referrers}"

if [[ ${FACTORY_RELEASE_ENV:-commercial} =~ ^gov[12]$ ]]; then
  : "${FACTORY_APPROVER_ID:?Gov signing requires an authenticated Jenkins approver}"
  : "${FACTORY_GOV_APPROVER_PATTERN:?FACTORY_GOV_APPROVER_PATTERN is required}"
  [[ ${FACTORY_APPROVER_ID} =~ ${FACTORY_GOV_APPROVER_PATTERN} ]] || {
    echo "Jenkins approver is not authorized for Gov signing" >&2
    exit 1
  }
fi

jq -e '.localDevelopment != true' "${work_dir}/resource-lock.json" >/dev/null
jq -e '.allow == true' "${evidence}/gate-result.json" >/dev/null
jq -e --arg digest "${IMPORTED_IMAGE_DIGEST}" '.digest == $digest' "${work_dir}/image-metadata.json" >/dev/null
subject="${IMPORTED_IMAGE_REF}@${IMPORTED_IMAGE_DIGEST}"
authdir=$(mktemp -d)
trap 'rm -rf "${authdir}"' EXIT
export DOCKER_CONFIG="${authdir}"
authfile="${authdir}/config.json"
printf '%s' "${ARTIFACTORY_SIGN_TOKEN}" | skopeo login --authfile "${authfile}" --username oidc --password-stdin \
  "${ARTIFACTORY_REGISTRY}"
export REGISTRY_AUTH_FILE="${authfile}"

# Cosign stores the signature and attestations beside the subject in
# Artifactory as digest-tagged Cosign attachments. The private key is supplied by a
# protected, release-job-scoped Jenkins file credential and is never uploaded.
cosign sign --yes --tlog-upload=false --key "${COSIGN_KEY_PATH}" "${subject}"

jq -n --arg digest "${IMPORTED_IMAGE_DIGEST}" --arg approver "${FACTORY_APPROVER_ID:-}" \
  --arg environment "${FACTORY_RELEASE_ENV:-commercial}" --arg approvedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{approvedAt:$approvedAt,digest:$digest,approver:$approver,environment:$environment}' >"${evidence}/approval.json"

scripts/generate_provenance.py "${work_dir}" "${evidence}/provenance.json"

declare -A predicates=(
  ["${work_dir}/resource-lock.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:resource-lock:v1"
  ["${evidence}/approval.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:approval:v1"
  ["${evidence}/sbom.cdx.json"]="https://cyclonedx.org/bom"
  ["${evidence}/sbom.spdx.json"]="https://spdx.dev/Document"
  ["${evidence}/provenance.json"]="https://slsa.dev/provenance/v1"
  ["${evidence}/findings.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:vulnerability:v1"
  ["${evidence}/compliance/result.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:compliance:v1"
  ["${evidence}/tests/result.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:test:v1"
  ["${evidence}/gate-result.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:gate:v1"
)
backend=$(jq -er '.scannerBackend // "fcs"' "${evidence}/gate-result.json")
case "${backend}" in
  fcs)
    predicates["${evidence}/scans/fcs/assessment.json"]="https://crowdstrike.com/fcs/image-assessment/v1"
    predicates["${evidence}/scans/fcs/sbom.cdx.json"]="https://cyclonedx.org/bom"
    predicates["${evidence}/scans/fcs/status.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:fcs-decision:v1"
    ;;
  grype)
    predicates["${evidence}/scans/grype/status.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:grype-decision:v1"
    predicates["${evidence}/scans/grype/report.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:grype-report:v1"
    ;;
  *) echo "Unknown signed scanner backend" >&2; exit 1 ;;
esac
for predicate in "${!predicates[@]}"; do
  cosign attest --yes --tlog-upload=false --key "${COSIGN_KEY_PATH}" \
    --type "${predicates[${predicate}]}" --predicate "${predicate}" "${subject}"
done

jq -n --arg subject "${subject}" --arg environment "${FACTORY_RELEASE_ENV:-commercial}" \
  --arg approver "${FACTORY_APPROVER_ID:-}" \
  '{signed:true,subject:$subject,environment:$environment,approver:$approver}'  >"${evidence}/signing-result.json"
unset COSIGN_PASSWORD ARTIFACTORY_SIGN_TOKEN REGISTRY_AUTH_FILE
