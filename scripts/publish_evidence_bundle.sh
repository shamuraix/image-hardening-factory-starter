#!/usr/bin/env bash
# Attach the candidate's evidence bundle to its quarantined image as an OCI
# referrer so the separate release PipelineRun can retrieve exactly the evidence
# that passed the gate. The bundle is unsigned here; the release pipeline pins
# it by manifest digest and tarball SHA-256 from the reviewed release request,
# then signs each predicate with the environment key.
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
: "${catalog}"
# shellcheck disable=SC1090,SC1091
source "${work_dir}/import.env"
: "${IMPORTED_IMAGE_REF:?}"
: "${IMPORTED_IMAGE_DIGEST:?}"
: "${ARTIFACTORY_REGISTRY:?}"
: "${ARTIFACTORY_WRITE_TOKEN:?}"
artifact_type=${FACTORY_EVIDENCE_ARTIFACT_TYPE:-application/vnd.image-hardening-factory.evidence.v1+tar}
layer_type=${FACTORY_EVIDENCE_LAYER_TYPE:-application/vnd.image-hardening-factory.evidence.layer.v1.tar+gzip}

jq -e '.allow == true' "${work_dir}/evidence/gate-result.json" >/dev/null || {
  echo "refusing to publish evidence for a candidate the gate denied" >&2
  exit 1
}

# Strip only a trailing :tag, never a registry :port.
repository=${IMPORTED_IMAGE_REF}
last_segment=${repository##*/}
[[ ${last_segment} == *:* ]] && repository=${repository%:*}
subject="${repository}@${IMPORTED_IMAGE_DIGEST}"

staging=$(mktemp -d)
authdir=$(mktemp -d)
trap 'rm -rf "${staging}" "${authdir}"' EXIT
bundle_dir="${staging}/bundle"
mkdir -p "${bundle_dir}"
for file in image-metadata.json resource-lock.json resource-lock.sig build.env \
  import.env import-result.json; do
  [[ -f "${work_dir}/${file}" ]] && cp "${work_dir}/${file}" "${bundle_dir}/"
done
cp -a "${work_dir}/evidence" "${bundle_dir}/evidence"
# Deterministic archive: identical evidence always yields the same digest.
tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
  -C "${bundle_dir}" -cf - . | gzip -n >"${staging}/evidence.tar.gz"
bundle_sha256=$(sha256sum "${staging}/evidence.tar.gz" | awk '{print $1}')

source scripts/lib/registry_auth.sh
factory_registry_auth "${authdir}"
printf '%s' "${ARTIFACTORY_WRITE_TOKEN}" | oras login --registry-config "${authdir}/config.json" \
  --username "${ARTIFACTORY_USERNAME:-oidc}" --password-stdin "${ARTIFACTORY_REGISTRY}" >/dev/null
(
  cd "${staging}"
  oras attach --registry-config "${authdir}/config.json" \
    --artifact-type "${artifact_type}" \
    --export-manifest manifest.json \
    "${subject}" "evidence.tar.gz:${layer_type}"
)
manifest_digest="sha256:$(sha256sum "${staging}/manifest.json" | awk '{print $1}')"

jq -n --arg subject "${subject}" --arg repository "${repository}" \
  --arg manifestDigest "${manifest_digest}" --arg sha256 "${bundle_sha256}" \
  --arg artifactType "${artifact_type}" \
  '{subject:$subject,repository:$repository,manifestDigest:$manifestDigest,sha256:$sha256,artifactType:$artifactType}' \
  >"${work_dir}/evidence-bundle.json"
cat "${work_dir}/evidence-bundle.json"
