#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
context="${work_dir}/context"
image=$(yq -r '.metadata.name' "${catalog}")
mirror_path=$(yq -r '.source.mirrorPath' "${catalog}")
revision=$(yq -r '.source.revision' "${catalog}")
overlay=$(yq -r '.source.overlay // ""' "${catalog}")
source_repository=${FACTORY_SOURCE_REPOSITORY:?FACTORY_SOURCE_REPOSITORY is required}
local_image_namespace=${LOCAL_IMAGE_NAMESPACE:-localhost/factory}
factory_workspace=${FACTORY_WORKSPACE:-${PWD}}

: "${INTERNAL_GIT_BASE_URL:?INTERNAL_GIT_BASE_URL is required}"
: "${ARTIFACTORY_URL:?ARTIFACTORY_URL is required}"
: "${ARTIFACTORY_READ_TOKEN:?ARTIFACTORY_READ_TOKEN is required}"
: "${COSIGN_INTAKE_PUBLIC_KEY:?COSIGN_INTAKE_PUBLIC_KEY is required}"

rm -rf "${context}"
git clone --filter=blob:none --no-checkout "${INTERNAL_GIT_BASE_URL%/}/${mirror_path}.git" "${context}"
git -C "${context}" checkout --detach "${revision}"
[[ $(git -C "${context}" rev-parse HEAD) == "${revision}" ]]

if [[ -n "${overlay}" ]]; then
  while IFS= read -r -d '' patch; do
    git -C "${context}" apply --check "${factory_workspace}/${patch}"
    git -C "${context}" apply "${factory_workspace}/${patch}"
  done < <(find "${overlay}" -type f -name '*.patch' -print0 | sort -z)
fi
scripts/validate_context.py "${catalog}" "${context}"

lock_url="${ARTIFACTORY_URL%/}/artifactory/${source_repository}/locks/${image}/${revision}/resource-lock.json"
curl --fail --silent --show-error --location \
  --header "Authorization: Bearer ${ARTIFACTORY_READ_TOKEN}" \
  --output "${work_dir}/resource-lock.json" "${lock_url}"
curl --fail --silent --show-error --location \
  --header "Authorization: Bearer ${ARTIFACTORY_READ_TOKEN}" \
  --output "${work_dir}/resource-lock.sig" "${lock_url%resource-lock.json}resource-lock.sig"
cosign verify-blob --key "${COSIGN_INTAKE_PUBLIC_KEY}" \
  --insecure-ignore-tlog --signature "${work_dir}/resource-lock.sig" \
  "${work_dir}/resource-lock.json" >/dev/null

jq -e --arg revision "${revision}" '.source.revision == $revision' "${work_dir}/resource-lock.json" >/dev/null
expected_snapshot=$(scripts/rpm_snapshot_id.sh "${catalog}")
jq -e --arg snapshot "${expected_snapshot}" '.rpmSnapshot.snapshotId == $snapshot' \
  "${work_dir}/resource-lock.json" >/dev/null
rpm_repomd_digest=$(jq -er '.rpmSnapshot.repomdDigest' "${work_dir}/resource-lock.json")

while IFS=$'\t' read -r filename path digest; do
  [[ -n "${filename}" ]] || continue
  curl --fail --silent --show-error --location \
    --header "Authorization: Bearer ${ARTIFACTORY_READ_TOKEN}" \
    --output "${context}/${filename}" \
    "${ARTIFACTORY_URL%/}/artifactory/${source_repository}/${path}"
  printf '%s  %s\n' "${digest#sha256:}" "${context}/${filename}" | sha256sum --check --status
done < <(jq -r '.resources[] | select(.kind == "file") | [.filename,.artifactoryPath,.contentDigest] | @tsv' "${work_dir}/resource-lock.json")

base_kind=$(yq -r '.build.base.kind' "${catalog}")
if [[ "${base_kind}" == catalog ]]; then
  base=$(yq -r '.build.base.image' "${catalog}")
  if [[ -f "work/${base}/image.oci.tar" ]]; then
    cp "work/${base}/image.oci.tar" "${work_dir}/base.oci.tar"
    base_ref="${local_image_namespace}/${base}:dependency"
    base_digest=$(jq -er '.digest' "work/${base}/image-metadata.json")
  else
    release_lock="${ARTIFACTORY_URL%/}/artifactory/${source_repository}/releases/${base}/current.json"
    base_ref=$(curl --fail --silent --show-error \
      --header "Authorization: Bearer ${ARTIFACTORY_READ_TOKEN}" "${release_lock}" | jq -er '.imageRef')
    base_digest=${base_ref##*@}
  fi
else
  lock_key=$(yq -r '.build.base.lockKey' "${catalog}")
  base_ref=$(jq -er --arg key "${lock_key}" \
    '.resources[] | select(.kind == "oci" and (.tag | contains($key|split("redhat-")[-1]|split("-minimal")[0]))) | .source' \
    "${work_dir}/resource-lock.json" | head -n1)
  base_ref=${base_ref#docker://}
  base_ref="${ARTIFACTORY_REGISTRY}/${UPSTREAM_OCI_REPOSITORY}/${base_ref#*/}"
  base_digest=${base_ref##*@}
fi

cat >"${work_dir}/build.env" <<EOF
FACTORY_IMAGE=${image}
SOURCE_REVISION=${revision}
BASE_REF=${base_ref}
BASE_DIGEST=${base_digest}
RPM_REPOMD_DIGEST=${rpm_repomd_digest}
EOF
