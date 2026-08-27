#!/usr/bin/env bash
set -euo pipefail

image=${1:?image name is required}
catalog="catalog/images/${image}.yaml"
revision=$(yq -r '.source.revision' "${catalog}")
work="work/${image}"
source_repository=${FACTORY_SOURCE_REPOSITORY:?FACTORY_SOURCE_REPOSITORY is required}
: "${ARTIFACTORY_URL:?}"
: "${ARTIFACTORY_WRITE_TOKEN:?}"
: "${COSIGN_INTAKE_KEY_REF:?}"
: "${COSIGN_INTAKE_PUBLIC_KEY:?}"

snapshot_id=$(scripts/rpm_snapshot_id.sh "${catalog}")
for file in snapshot.json snapshot.sig; do
  curl --fail --silent --show-error \
    --header "Authorization: Bearer ${ARTIFACTORY_WRITE_TOKEN}" \
    --output "${work}/${file}" \
    "${ARTIFACTORY_URL%/}/artifactory/${source_repository}/rpm-snapshots/${snapshot_id}/${file}"
done
cosign verify-blob --key "${COSIGN_INTAKE_PUBLIC_KEY}" \
  --insecure-ignore-tlog --signature "${work}/snapshot.sig" "${work}/snapshot.json" >/dev/null
jq --slurpfile snapshot "${work}/snapshot.json" '. + {rpmSnapshot:$snapshot[0]}' \
  "${work}/resource-lock.json" >"${work}/resource-lock.tmp.json"
mv "${work}/resource-lock.tmp.json" "${work}/resource-lock.json"

while IFS=$'\t' read -r filename path digest; do
  [[ -n "${filename}" ]] || continue
  clamscan --database=/opt/security-data/clamav --infected "${work}/cache/${filename}" \
    >"${work}/cache/${filename}.clamav.txt"
  curl --fail --silent --show-error --request PUT \
    --header "Authorization: Bearer ${ARTIFACTORY_WRITE_TOKEN}" \
    --header "X-Checksum-Sha256: ${digest#sha256:}" \
    --upload-file "${work}/cache/${filename}" \
    "${ARTIFACTORY_URL%/}/artifactory/${source_repository}/${path}"
done < <(jq -r '.resources[] | select(.kind == "file") | [.filename,.artifactoryPath,.contentDigest] | @tsv' "${work}/resource-lock.json")

cosign sign-blob --yes --tlog-upload=false --key "${COSIGN_INTAKE_KEY_REF}" \
  --output-signature "${work}/resource-lock.sig" "${work}/resource-lock.json"
for file in resource-lock.json resource-lock.sig; do
  curl --fail --silent --show-error --request PUT \
    --header "Authorization: Bearer ${ARTIFACTORY_WRITE_TOKEN}" \
    --upload-file "${work}/${file}" \
    "${ARTIFACTORY_URL%/}/artifactory/${source_repository}/locks/${image}/${revision}/${file}"
done

# OCI archives are imported by digest with Skopeo. The destination name is
# derived from the source repository while the digest remains authoritative.
while IFS=$'\t' read -r source digest; do
  [[ -n "${source}" ]] || continue
  source=${source#docker://}
  path=${source%%@*}
  skopeo copy --all "docker://${source}" \
    "docker://${ARTIFACTORY_REGISTRY}/${UPSTREAM_OCI_REPOSITORY}/${path#*/}:${digest#sha256:}"
done < <(jq -r '.resources[] | select(.kind == "oci") | [.source,.declaredDigest] | @tsv' "${work}/resource-lock.json")
