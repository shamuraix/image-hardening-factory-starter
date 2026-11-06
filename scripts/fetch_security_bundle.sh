#!/usr/bin/env bash
# Download the current security-data bundle into <work-dir>/security-data and
# verify it before any stage uses it: pointer digest, intake signature, and the
# bundle's own manifest. Runs in the prepare stage, which already holds the
# Artifactory read token and the intake public key.
set -euo pipefail

work_dir=${1:?work directory is required}
: "${ARTIFACTORY_URL:?}"
: "${ARTIFACTORY_READ_TOKEN:?}"
: "${COSIGN_INTAKE_PUBLIC_KEY:?}"
source_repository=${FACTORY_SOURCE_REPOSITORY:?FACTORY_SOURCE_REPOSITORY is required}
base="${ARTIFACTORY_URL%/}/artifactory/${source_repository}"
destination="${work_dir}/security-data"
staging=$(mktemp -d)
trap 'rm -rf "${staging}"' EXIT

get() {
  curl --fail --silent --show-error --location \
    --header "Authorization: Bearer ${ARTIFACTORY_READ_TOKEN}" --output "${2}" "${1}"
}
get "${base}/security-data/current.json" "${staging}/current.json"
path=$(jq -er '.path' "${staging}/current.json")
expected=$(jq -er '.sha256' "${staging}/current.json")
[[ ${path} =~ ^security-data/[0-9TZ]+/security-data\.tar\.gz$ ]] || { echo "invalid bundle path" >&2; exit 1; }
[[ ${expected} =~ ^[0-9a-f]{64}$ ]] || { echo "invalid bundle digest" >&2; exit 1; }
get "${base}/${path}" "${staging}/bundle.tar.gz"
get "${base}/${path}.sig" "${staging}/bundle.tar.gz.sig"
observed=$(sha256sum "${staging}/bundle.tar.gz" | awk '{print $1}')
[[ ${observed} == "${expected}" ]] || { echo "security-data bundle digest mismatch" >&2; exit 1; }
cosign verify-blob --key "${COSIGN_INTAKE_PUBLIC_KEY}" --insecure-ignore-tlog \
  --signature "${staging}/bundle.tar.gz.sig" "${staging}/bundle.tar.gz" >/dev/null

rm -rf "${destination}"
mkdir -p "${destination}"
tar --no-same-owner -xzf "${staging}/bundle.tar.gz" -C "${destination}"
(cd "${destination}" && sha256sum --check --strict --quiet manifest.sha256)
echo "security-data $(cat "${destination}/generated-at") verified"
