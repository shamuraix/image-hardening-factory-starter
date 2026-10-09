#!/usr/bin/env bash
# Upload a built security-data bundle and point security-data/current.json at
# it. The pointer is written last, so readers only ever see complete uploads.
set -euo pipefail

output=${1:?bundle directory (as passed to build_security_bundle.sh) is required}
: "${ARTIFACTORY_URL:?}"
: "${ARTIFACTORY_WRITE_TOKEN:?}"
source_repository=${FACTORY_SOURCE_REPOSITORY:?FACTORY_SOURCE_REPOSITORY is required}
archive="${output}.tar.gz"
[[ -s ${archive} && -s ${archive}.sig ]] || { echo "bundle or signature missing" >&2; exit 1; }

generated_at=$(cat "${output}/generated-at")
stamp=$(tr -d ':-' <<<"${generated_at}")
base="${ARTIFACTORY_URL%/}/artifactory/${source_repository}/security-data"
sha256=$(sha256sum "${archive}" | awk '{print $1}')
for file in "${archive}" "${archive}.sig"; do
  name=security-data.tar.gz
  [[ ${file} == *.sig ]] && name=security-data.tar.gz.sig
  curl --fail --silent --show-error --request PUT \
    --header "Authorization: Bearer ${ARTIFACTORY_WRITE_TOKEN}" \
    --header "X-Checksum-Sha256: $(sha256sum "${file}" | awk '{print $1}')" \
    --upload-file "${file}" "${base}/${stamp}/${name}"
done
pointer=$(mktemp)
trap 'rm -f "${pointer}"' EXIT
jq -n --arg path "security-data/${stamp}/security-data.tar.gz" --arg sha256 "${sha256}" \
  --arg generatedAt "${generated_at}" '{path:$path,sha256:$sha256,generatedAt:$generatedAt}' >"${pointer}"
curl --fail --silent --show-error --request PUT \
  --header "Authorization: Bearer ${ARTIFACTORY_WRITE_TOKEN}" \
  --header "Content-Type: application/json" \
  --upload-file "${pointer}" "${base}/current.json"
echo "published security-data ${generated_at} (sha256 ${sha256})"
