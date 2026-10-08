#!/usr/bin/env bash
# Retrieve and verify the evidence bundle named by a release request, restoring
# work/<image>/ exactly as the build PipelineRun left it after quarantine import.
set -euo pipefail

request=${1:?release request JSON is required}
work_dir=${2:?work directory is required}
: "${ARTIFACTORY_REGISTRY:?}"
: "${ARTIFACTORY_READ_TOKEN:?}"

repository=$(jq -er '.candidate.repository' "${request}")
image_digest=$(jq -er '.candidate.digest' "${request}")
manifest_digest=$(jq -er '.evidence.manifestDigest' "${request}")
expected_sha=$(jq -er '.evidence.sha256' "${request}")
[[ ${image_digest} =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "invalid candidate digest" >&2; exit 2; }
[[ ${manifest_digest} =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "invalid evidence digest" >&2; exit 2; }
[[ ${expected_sha} =~ ^[0-9a-f]{64}$ ]] || { echo "invalid evidence sha256" >&2; exit 2; }
[[ ${repository} == "${ARTIFACTORY_REGISTRY}/"* ]] || {
  echo "candidate repository is outside the internal registry" >&2; exit 2;
}

staging=$(mktemp -d)
authdir=$(mktemp -d)
trap 'rm -rf "${staging}" "${authdir}"' EXIT
source scripts/lib/registry_auth.sh
factory_registry_auth "${authdir}"
printf '%s' "${ARTIFACTORY_READ_TOKEN}" | oras login --registry-config "${authdir}/config.json" \
  --username oidc --password-stdin "${ARTIFACTORY_REGISTRY}" >/dev/null

# The evidence manifest must be a referrer of the requested candidate digest.
oras manifest fetch --registry-config "${authdir}/config.json" \
  "${repository}@${manifest_digest}" >"${staging}/manifest.json"
jq -e --arg digest "${image_digest}" '.subject.digest == $digest' "${staging}/manifest.json" >/dev/null || {
  echo "evidence manifest is not attached to ${image_digest}" >&2; exit 1;
}
oras pull --registry-config "${authdir}/config.json" --output "${staging}" \
  "${repository}@${manifest_digest}" >/dev/null
observed=$(sha256sum "${staging}/evidence.tar.gz" | awk '{print $1}')
[[ ${observed} == "${expected_sha}" ]] || {
  echo "evidence bundle sha256 mismatch: expected ${expected_sha}, found ${observed}" >&2; exit 1;
}

mkdir -p "${work_dir}"
tar --no-same-owner -xzf "${staging}/evidence.tar.gz" -C "${work_dir}"
jq -e --arg digest "${image_digest}" '.digest == $digest' "${work_dir}/image-metadata.json" >/dev/null
jq -e '.allow == true' "${work_dir}/evidence/gate-result.json" >/dev/null
# shellcheck disable=SC1090,SC1091
source "${work_dir}/import.env"
[[ ${IMPORTED_IMAGE_DIGEST:-} == "${image_digest}" ]] || {
  echo "import identity does not match the release request" >&2; exit 1;
}
echo "evidence for ${repository}@${image_digest} verified"
