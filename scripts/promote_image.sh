#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
# shellcheck disable=SC1090,SC1091
source "${work_dir}/import.env"
: "${IMPORTED_IMAGE_REF:?}"
: "${IMPORTED_IMAGE_DIGEST:?}"
: "${COSIGN_PUBLIC_KEY:?}"
: "${ARTIFACTORY_RELEASE_TOKEN:?}"

if [[ ${FACTORY_RELEASE_ENV:-commercial} =~ ^gov[12]$ ]]; then
  : "${FACTORY_APPROVER_ID:?Gov promotion requires an authenticated Jenkins approver}"
  : "${FACTORY_GOV_APPROVER_PATTERN:?FACTORY_GOV_APPROVER_PATTERN is required}"
  [[ ${FACTORY_APPROVER_ID} =~ ${FACTORY_GOV_APPROVER_PATTERN} ]] || {
    echo "Jenkins approver is not authorized for Gov promotion" >&2
    exit 1
  }
fi

authdir=$(mktemp -d)
trap 'rm -rf "${authdir}"' EXIT
export DOCKER_CONFIG="${authdir}"
export REGISTRY_AUTH_FILE="${authdir}/config.json"
printf '%s' "${ARTIFACTORY_RELEASE_TOKEN}" | skopeo login --authfile "${REGISTRY_AUTH_FILE}" \
  --username oidc --password-stdin "${ARTIFACTORY_REGISTRY}"
source_ref="${IMPORTED_IMAGE_REF}@${IMPORTED_IMAGE_DIGEST}"
scripts/verify_release_evidence.sh "${source_ref}"

release_repository=$(scripts/catalog_value.sh "${catalog}" '.publication.releaseRepository')
path=${FACTORY_IMAGE_PATH:-$(scripts/catalog_value.sh "${catalog}" '.publication.imagePath')}
version=$(yq -r '.product.version' "${catalog}")
destination="${ARTIFACTORY_REGISTRY}/${release_repository}/${path}:${version}-${IMPORTED_IMAGE_DIGEST#sha256:}"

oras cp --from-plain-http=false --to-plain-http=false --from-registry-config "${REGISTRY_AUTH_FILE}" --to-registry-config "${REGISTRY_AUTH_FILE}" --recursive "${source_ref}" "${destination}"
# Cosign 2.x stores signatures/attestations under digest-derived tags, which
# ORAS recursive referrer discovery alone does not copy.
# A retry may encounter a complete, already verified destination. Do not force
# overwrite existing signatures from another signer or an incomplete release.
if ! scripts/verify_release_evidence.sh "${destination}@${IMPORTED_IMAGE_DIGEST}" >/dev/null 2>&1; then
  cosign copy --only=sig,att,sbom "${source_ref}" "${destination}"
fi
observed=$(skopeo inspect --authfile "${REGISTRY_AUTH_FILE}" "docker://${destination}" | jq -er '.Digest')
[[ "${observed}" == "${IMPORTED_IMAGE_DIGEST}" ]] || {
  echo "digest changed during promotion: ${IMPORTED_IMAGE_DIGEST} -> ${observed}" >&2
  exit 1
}
scripts/verify_release_evidence.sh "${destination}@${observed}"
jq -n --arg imageRef "${destination}" --arg digest "${observed}" \
  '{promoted:true,imageRef:$imageRef,digest:$digest}' >"${work_dir}/promotion-result.json"
