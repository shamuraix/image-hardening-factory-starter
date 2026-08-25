#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
# shellcheck disable=SC1090,SC1091
source "${work_dir}/build.env"
scripts/require_rootless.sh buildah

containerfile=$(yq -r '.source.containerfile' "${catalog}")
platform=$(yq -r '.build.platforms[0]' "${catalog}")
created=$(git -C "${work_dir}/context" show -s --format=%cI "${SOURCE_REVISION}")
local_ref="localhost/factory/${FACTORY_IMAGE}:${CI_PIPELINE_ID:-local}"
isolation=${BUILDAH_ISOLATION:-rootless}
[[ "${isolation}" == rootless ]] || {
  echo "BUILDAH_ISOLATION must be rootless" >&2
  exit 2
}
skopeo_copy_args=(--retry-times 5)
if [[ ${FACTORY_REMOVE_TRANSPORT_SIGNATURES:-false} == true ]]; then
  skopeo_copy_args+=(--remove-signatures)
fi

if [[ -f "${work_dir}/base.oci.tar" ]]; then
  skopeo copy "${skopeo_copy_args[@]}" \
    "oci-archive:${work_dir}/base.oci.tar" "containers-storage:${BASE_REF}"
fi

args=(
  --file "${work_dir}/context/${containerfile}"
  --tag "${local_ref}"
  --isolation "${isolation}"
  --network "${FACTORY_BUILD_NETWORK:-slirp4netns}"
  --pull=never
  --platform "${platform}"
  --timestamp "$(date --date="${created}" +%s)"
  --build-arg "BASE_REF=${BASE_REF}"
  --label "org.opencontainers.image.revision=${SOURCE_REVISION}"
  --label "org.opencontainers.image.created=${created}"
  --label "org.opencontainers.image.source=${CI_PROJECT_URL:-local}"
)

if [[ -n ${FACTORY_BASE_MAJOR:-} ]]; then
  base_major=${FACTORY_BASE_MAJOR}
elif [[ $(yq -r '.build.base.kind' "${catalog}") == catalog ]]; then
  base_catalog="catalog/images/$(yq -r '.build.base.image' "${catalog}").yaml"
  base_major=$(yq -r '.product.version | split(".")[0]' "${base_catalog}")
else
  base_major=$(yq -r '.product.version | split(".")[0]' "${catalog}")
fi
args+=(--build-arg "BASE_MAJOR=${base_major}")

repo_dir=$(mktemp -d)
trap 'rm -rf "${repo_dir}"' EXIT
RPM_SNAPSHOT_ID=$(scripts/write_repo_config.sh "${catalog}" "${repo_dir}/factory.repo")
export RPM_SNAPSHOT_ID
# Mount only the factory repository file. Mounting the whole directory
# read-only prevents librhsm from creating its generated redhat.repo file and
# causes microdnf to fail before it can use the explicitly enabled UBI repos.
args+=(--volume "${repo_dir}/factory.repo:/etc/yum.repos.d/factory.repo:ro,Z")

while IFS=$'\t' read -r key value; do
  args+=(--build-arg "${key}=${value}")
done < <(yq -r '.build.buildArgs // {} | to_entries[] | [.key,.value] | @tsv' "${catalog}")

buildah bud "${args[@]}" "${work_dir}/context"
# Preserve the exact local reference in the archive so downstream jobs can
# load and select this image deterministically, even when unrelated or dangling
# images exist in container storage.
skopeo copy "${skopeo_copy_args[@]}" \
  "containers-storage:${local_ref}" "oci-archive:${work_dir}/image.oci.tar:${local_ref}"
# The archive is the candidate handed to every scanner and importer, so record
# its manifest digest rather than assuming a storage-to-archive copy preserved
# the source manifest byte-for-byte.
digest=$(skopeo inspect "oci-archive:${work_dir}/image.oci.tar:${local_ref}" | jq -er '.Digest')

jq -n \
  --arg image "${FACTORY_IMAGE}" \
  --arg digest "${digest}" \
  --arg sourceRevision "${SOURCE_REVISION}" \
  --arg baseRef "${BASE_REF}" \
  --arg baseDigest "${BASE_DIGEST}" \
  --arg factoryRevision "${CI_COMMIT_SHA:-local}" \
  --arg rpmSnapshot "${RPM_SNAPSHOT_ID}" \
  --arg rpmRepomdDigest "${RPM_REPOMD_DIGEST}" \
  --arg created "${created}" \
  '{image:$image,digest:$digest,sourceRevision:$sourceRevision,baseRef:$baseRef,baseDigest:$baseDigest,factoryRevision:$factoryRevision,rpmSnapshot:$rpmSnapshot,rpmRepomdDigest:$rpmRepomdDigest,created:$created}' \
  >"${work_dir}/image-metadata.json"
printf 'IMAGE_DIGEST=%s\nLOCAL_IMAGE_REF=%s\n' "${digest}" "${local_ref}" >>"${work_dir}/build.env"
