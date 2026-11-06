#!/usr/bin/env bash
# Publish releases/<image>/current.json, the pointer prepare_context.sh reads
# when a dependent image builds against an already-released catalog base.
# Before the Tekton migration nothing wrote this file, so application builds
# that did not also rebuild their base could not resolve one.
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
image=$(yq -er '.metadata.name' "${catalog}")
pointer_environment=${FACTORY_POINTER_ENVIRONMENT:-commercial}
catalog_dir=$(dirname "${catalog}")

dependents=$(PYTHONPATH="${PYTHONPATH:-.}" python3 - "${catalog_dir}" "${image}" <<'PYTHON'
import sys
from factory.catalog import load_catalog
images = load_catalog(sys.argv[1])
print(",".join(sorted(n for n, d in images.items() if d.base_image == sys.argv[2])))
PYTHON
)
if [[ -z ${dependents} || ${FACTORY_RELEASE_ENV:-commercial} != "${pointer_environment}" ]]; then
  echo "no release pointer for ${image} (dependents='${dependents}', environment=${FACTORY_RELEASE_ENV:-commercial})"
  jq -n '{published:false}' >"${work_dir}/release-pointer.json"
  exit 0
fi

: "${ARTIFACTORY_URL:?}"
: "${ARTIFACTORY_POINTER_TOKEN:?}"
source_repository=${FACTORY_SOURCE_REPOSITORY:?FACTORY_SOURCE_REPOSITORY is required}
reference=$(jq -er '.imageRef' "${work_dir}/promotion-result.json")
digest=$(jq -er '.digest' "${work_dir}/promotion-result.json")
[[ ${digest} =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "invalid promoted digest" >&2; exit 1; }
# Digest-qualified so builds can never drift to a re-tagged image.
image_ref="${reference%:*}@${digest}"
jq -n --arg image "${image}" --arg imageRef "${image_ref}" --arg digest "${digest}" \
  --arg environment "${pointer_environment}" --arg promotedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg dependents "${dependents}" \
  '{image:$image,imageRef:$imageRef,digest:$digest,environment:$environment,promotedAt:$promotedAt,
    dependents:($dependents|split(",")),published:true}' >"${work_dir}/release-pointer.json"
curl --fail --silent --show-error --request PUT \
  --header "Authorization: Bearer ${ARTIFACTORY_POINTER_TOKEN}" \
  --header "Content-Type: application/json" \
  --upload-file "${work_dir}/release-pointer.json" \
  "${ARTIFACTORY_URL%/}/artifactory/${source_repository}/releases/${image}/current.json"
echo "published release pointer ${image} -> ${image_ref}"
