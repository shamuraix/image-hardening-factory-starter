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

subject="${IMPORTED_IMAGE_REF}@${IMPORTED_IMAGE_DIGEST}"
authfile=$(mktemp)
trap 'rm -f "${authfile}"' EXIT
skopeo login --authfile "${authfile}" --username oidc --password "${ARTIFACTORY_SIGN_TOKEN}" \
  "${ARTIFACTORY_REGISTRY}"
export REGISTRY_AUTH_FILE="${authfile}"

# Cosign stores the signature and attestations beside the subject in
# Artifactory as OCI artifacts/referrers. The private key is supplied by a
# protected, release-job-scoped Jenkins file credential and is never uploaded.
cosign sign --yes --tlog-upload=false --key "${COSIGN_KEY_PATH}" "${subject}"

scripts/generate_provenance.py "${work_dir}" "${evidence}/provenance.json"

declare -A predicates=(
  ["${evidence}/sbom.cdx.json"]="https://cyclonedx.org/bom"
  ["${evidence}/sbom.spdx.json"]="https://spdx.dev/Document"
  ["${evidence}/provenance.json"]="https://slsa.dev/provenance/v1"
  ["${evidence}/findings.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:vulnerability:v1"
  ["${evidence}/scans/fcs/assessment.json"]="https://crowdstrike.com/fcs/image-assessment/v1"
  ["${evidence}/scans/fcs/sbom.cdx.json"]="https://cyclonedx.org/bom"
  ["${evidence}/scans/fcs/status.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:fcs-decision:v1"
  ["${evidence}/compliance/result.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:compliance:v1"
  ["${evidence}/tests/result.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:test:v1"
  ["${evidence}/gate-result.json"]="${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}:gate:v1"
)
for predicate in "${!predicates[@]}"; do
  cosign attest --yes --tlog-upload=false --key "${COSIGN_KEY_PATH}" \
    --type "${predicates[${predicate}]}" --predicate "${predicate}" "${subject}"
done

jq -n --arg subject "${subject}" --arg environment "${FACTORY_RELEASE_ENV:-commercial}" \
  '{signed:true,subject:$subject,environment:$environment}' >"${evidence}/signing-result.json"
unset COSIGN_PASSWORD ARTIFACTORY_SIGN_TOKEN REGISTRY_AUTH_FILE
