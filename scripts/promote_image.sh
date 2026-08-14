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
  [[ ${GITLAB_USER_EMAIL:-} == *.us ]] || { echo "Gov promotion requires a .us approver" >&2; exit 1; }
fi

source_ref="${IMPORTED_IMAGE_REF}@${IMPORTED_IMAGE_DIGEST}"
cosign verify --key "${COSIGN_PUBLIC_KEY}" --insecure-ignore-tlog "${source_ref}" >/dev/null

release_repository=$(yq -r '.publication.releaseRepository' "${catalog}")
path=$(yq -r '.publication.imagePath' "${catalog}")
version=$(yq -r '.product.version' "${catalog}")
destination="${ARTIFACTORY_REGISTRY}/${release_repository}/${path}:${version}"

oras login --username oidc --password "${ARTIFACTORY_RELEASE_TOKEN}" "${ARTIFACTORY_REGISTRY}"
oras cp --recursive "${source_ref}" "${destination}"
observed=$(skopeo inspect --creds "oidc:${ARTIFACTORY_RELEASE_TOKEN}" "docker://${destination}" | jq -er '.Digest')
[[ "${observed}" == "${IMPORTED_IMAGE_DIGEST}" ]] || {
  echo "digest changed during promotion: ${IMPORTED_IMAGE_DIGEST} -> ${observed}" >&2
  exit 1
}
cosign verify --key "${COSIGN_PUBLIC_KEY}" --insecure-ignore-tlog "${destination}@${observed}" >/dev/null
