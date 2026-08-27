#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
jq -e '.localDevelopment != true' "${work_dir}/resource-lock.json" >/dev/null || {
  echo "local development locks cannot be imported to quarantine" >&2
  exit 1
}
allowed=$(jq -er '.allow' "${work_dir}/evidence/gate-result.json")
[[ "${allowed}" == true ]] || { echo "release gate denied import" >&2; exit 1; }

if [[ ${FACTORY_PROTECTED_PUBLISH:-false} != true ]]; then
  jq -n --arg image "${FACTORY_IMAGE}" '{imported:false,candidateOnly:true,image:$image}' \
    >"${work_dir}/import-result.json"
  printf 'IMPORTED_IMAGE_REF=oci-archive:%s/image.oci.tar\n' "${work_dir}" >"${work_dir}/import.env"
  exit 0
fi

: "${ARTIFACTORY_REGISTRY:?}"
: "${ARTIFACTORY_WRITE_TOKEN:?}"
repository=${FACTORY_QUARANTINE_REPOSITORY:-$(
  scripts/catalog_value.sh "${catalog}" '.publication.quarantineRepository'
)}
path=${FACTORY_IMAGE_PATH:-$(scripts/catalog_value.sh "${catalog}" '.publication.imagePath')}
version=$(yq -r '.product.version' "${catalog}")
build_id=${FACTORY_BUILD_ID:?FACTORY_BUILD_ID is required for protected publication}
[[ "${build_id}" =~ ^[A-Za-z0-9_.-]+$ ]] || {
  echo "FACTORY_BUILD_ID contains characters that are invalid in an OCI tag" >&2
  exit 2
}
destination="${ARTIFACTORY_REGISTRY}/${repository}/${path}:${version}-${build_id}"
candidate_digest=$(jq -er '.digest' "${work_dir}/image-metadata.json")
authfile=$(mktemp)
trap 'rm -f "${authfile}"' EXIT
skopeo login --authfile "${authfile}" --username oidc --password "${ARTIFACTORY_WRITE_TOKEN}" "${ARTIFACTORY_REGISTRY}"
skopeo copy --preserve-digests --authfile "${authfile}" \
  "oci-archive:${work_dir}/image.oci.tar" "docker://${destination}"
digest=$(skopeo inspect --authfile "${authfile}" "docker://${destination}" | jq -er '.Digest')
[[ "${digest}" == "${candidate_digest}" ]] || {
  echo "digest changed during quarantine import: ${candidate_digest} -> ${digest}" >&2
  exit 1
}

jq -n --arg imageRef "${destination}" --arg digest "${digest}" \
  '{imported:true,imageRef:$imageRef,digest:$digest}' >"${work_dir}/import-result.json"
printf 'IMPORTED_IMAGE_REF=%s\nIMPORTED_IMAGE_DIGEST=%s\n' "${destination}" "${digest}" >"${work_dir}/import.env"
