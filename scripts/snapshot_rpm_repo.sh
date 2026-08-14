#!/usr/bin/env bash
set -euo pipefail

source_repo=${1:?source .repo file is required}
repository=${2:?Artifactory repository is required}
output=${3:?output directory is required}
repo_id=${4:?source repository id is required}
: "${ARTIFACTORY_URL:?}"
: "${ARTIFACTORY_WRITE_TOKEN:?}"
: "${COSIGN_INTAKE_KEY_REF:?}"

mkdir -p "${output}/content"
dnf reposync --config "${source_repo}" --repoid "${repo_id}" --download-metadata --download-path "${output}/content" \
  --arch x86_64 --arch noarch --newest-only --delete
createrepo_c --update "${output}/content"
repomd=$(find "${output}/content" -path '*/repodata/repomd.xml' -print -quit)
[[ -s "${repomd}" ]]
repomd_digest=$(sha256sum "${repomd}" | cut -d' ' -f1)
snapshot_id="$(date -u +%Y%m%dT%H%M%SZ)-${repomd_digest:0:16}"

while IFS= read -r -d '' file; do
  relative=${file#"${output}/content/"}
  curl --fail --silent --show-error --request PUT \
    --header "Authorization: Bearer ${ARTIFACTORY_WRITE_TOKEN}" \
    --header "X-Checksum-Sha256: $(sha256sum "${file}" | cut -d' ' -f1)" \
    --upload-file "${file}" \
    "${ARTIFACTORY_URL%/}/artifactory/${repository}/${snapshot_id}/${relative}"
done < <(find "${output}/content" -type f -print0 | sort -z)

jq -n --arg snapshotId "${snapshot_id}" --arg repomdDigest "sha256:${repomd_digest}" \
  --arg repository "${repository}" \
  '{snapshotId:$snapshotId,repository:$repository,repomdDigest:$repomdDigest}' \
  >"${output}/snapshot.json"
cosign sign-blob --yes --tlog-upload=false --key "${COSIGN_INTAKE_KEY_REF}" \
  --output-signature "${output}/snapshot.sig" "${output}/snapshot.json"
for file in snapshot.json snapshot.sig; do
  curl --fail --silent --show-error --request PUT \
    --header "Authorization: Bearer ${ARTIFACTORY_WRITE_TOKEN}" \
    --upload-file "${output}/${file}" \
    "${ARTIFACTORY_URL%/}/artifactory/generic-source-local/rpm-snapshots/${snapshot_id}/${file}"
done
printf '%s\n' "${snapshot_id}"
