#!/usr/bin/env bash
set -euo pipefail

# Usage: snapshot_rpm_repo.sh <source.repo> <artifactory-repository> <output-dir> <repo-id> [<repo-id>...]
#
# Accepts one or more repository IDs.  All channels are synced into a single
# combined snapshot directory and re-indexed as one immutable repository.

source_repo=${1:?source .repo file is required}
repository=${2:?Artifactory repository is required}
output=${3:?output directory is required}
shift 3
if [[ $# -eq 0 ]]; then
  echo "at least one source repository id is required" >&2
  exit 2
fi
repo_ids=("$@")
source_repository=${FACTORY_SOURCE_REPOSITORY:?FACTORY_SOURCE_REPOSITORY is required}
: "${ARTIFACTORY_URL:?}"
: "${ARTIFACTORY_WRITE_TOKEN:?}"
: "${COSIGN_INTAKE_KEY_REF:?}"

mkdir -p "${output}"/{content,dnf-cache,dnf-persist,dnf-log}

repoid_args=()
for id in "${repo_ids[@]}"; do
  [[ "${id}" =~ ^[A-Za-z0-9_.:/-]+$ ]] || {
    echo "invalid repository id: ${id}" >&2; exit 2
  }
  repoid_args+=(--repoid "${id}")
done

dnf reposync --config "${source_repo}" "${repoid_args[@]}" --download-metadata \
  --download-path "${output}/content" \
  --setopt="cachedir=${output}/dnf-cache" \
  --setopt="persistdir=${output}/dnf-persist" \
  --setopt="logdir=${output}/dnf-log" \
  --arch x86_64 --arch noarch --newest-only --delete
createrepo_c --update "${output}/content"
repomd=$(find "${output}/content" -path '*/repodata/repomd.xml' -print -quit)
[[ -s "${repomd}" ]]
repomd_digest=$(sha256sum "${repomd}" | cut -d' ' -f1)
snapshot_id="$(date -u +%Y%m%dT%H%M%SZ)-${repomd_digest:0:16}"

snapshot_bytes=$(du -sb "${output}/content" | cut -f1)
snapshot_files=$(find "${output}/content" -type f | wc -l)
printf 'snapshot size: %s bytes, %s files\n' "${snapshot_bytes}" "${snapshot_files}" >&2

while IFS= read -r -d '' file; do
  relative=${file#"${output}/content/"}
  curl --fail --silent --show-error --request PUT \
    --header "Authorization: ******" \
    --header "X-Checksum-Sha256: $(sha256sum "${file}" | cut -d' ' -f1)" \
    --upload-file "${file}" \
    "${ARTIFACTORY_URL%/}/artifactory/${repository}/${snapshot_id}/${relative}"
done < <(find "${output}/content" -type f -print0 | sort -z)

jq -n --arg snapshotId "${snapshot_id}" --arg repomdDigest "sha256:${repomd_digest}" \
  --arg repository "${repository}" \
  --argjson snapshotBytes "${snapshot_bytes}" --argjson snapshotFiles "${snapshot_files}" \
  '{snapshotId:$snapshotId,repository:$repository,repomdDigest:$repomdDigest,snapshotBytes:$snapshotBytes,snapshotFiles:$snapshotFiles}' \
  >"${output}/snapshot.json"
cosign sign-blob --yes --tlog-upload=false --key "${COSIGN_INTAKE_KEY_REF}" \
  --output-signature "${output}/snapshot.sig" "${output}/snapshot.json"
for file in snapshot.json snapshot.sig; do
  curl --fail --silent --show-error --request PUT \
    --header "Authorization: ******" \
    --upload-file "${output}/${file}" \
    "${ARTIFACTORY_URL%/}/artifactory/${source_repository}/rpm-snapshots/${snapshot_id}/${file}"
done
printf '%s\n' "${snapshot_id}"
