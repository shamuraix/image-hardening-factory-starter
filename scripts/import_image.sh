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

if [[ ${CI_PIPELINE_SOURCE:-local} == merge_request_event ]]; then
  jq -n --arg image "${FACTORY_IMAGE}" '{imported:false,candidateOnly:true,image:$image}' \
    >"${work_dir}/import-result.json"
  printf 'IMPORTED_IMAGE_REF=oci-archive:%s/image.oci.tar\n' "${work_dir}" >"${work_dir}/import.env"
  exit 0
fi

: "${ARTIFACTORY_REGISTRY:?}"
: "${ARTIFACTORY_WRITE_TOKEN:?}"
repository=${FACTORY_QUARANTINE_REPOSITORY:-$(yq -r '.publication.quarantineRepository' "${catalog}")}
path=${FACTORY_IMAGE_PATH:-$(yq -r '.publication.imagePath' "${catalog}")}
version=$(yq -r '.product.version' "${catalog}")
destination="${ARTIFACTORY_REGISTRY}/${repository}/${path}:${version}-${CI_PIPELINE_ID:-local}"
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
