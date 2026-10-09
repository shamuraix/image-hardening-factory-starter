#!/usr/bin/env bash
# Harness stand-in for scripts/prepare_context.sh, run by the real factory-stage
# Task inside the runner pod. The disposable cluster has no Artifactory, no
# internal git mirrors, and no intake key, so this clones the pinned Iron Bank
# revision from Repo One directly, applies the overlays, and pulls the public
# UBI image that matches the catalog as the base. The resource lock it writes is
# unsigned and marked localDevelopment, so the candidate can never be imported,
# signed, or promoted. Everything downstream (build, sbom, test) is production
# code.
set -euo pipefail
catalog=${FACTORY_CATALOG_FILE:?}
work=${FACTORY_WORK_DIR:?}
context="${work}/context"

image=$(yq -r '.metadata.name' "${catalog}")
upstream=$(yq -r '.source.upstream' "${catalog}")
revision=$(yq -r '.source.revision' "${catalog}")
overlay=$(yq -r '.source.overlay // ""' "${catalog}")
[[ $(yq -r '.build.base.kind' "${catalog}") == upstream ]] || {
  echo "the harness builds base images only (build.base.kind: upstream); ${image} is not one" >&2
  exit 2
}
major=$(yq -r '.product.version | split(".")[0]' "${catalog}")
version=$(yq -r '.product.version' "${catalog}")
base_ref="registry.access.redhat.com/ubi${major}/ubi-minimal:${version}"
platform=$(yq -r '.build.platforms[0]' "${catalog}")

rm -rf "${context}"
git clone --quiet --filter=blob:none --no-checkout "${upstream}" "${context}"
git -C "${context}" checkout --quiet --detach "${revision}"
[[ $(git -C "${context}" rev-parse HEAD) == "${revision}" ]]
if [[ -n ${overlay} ]]; then
  while IFS= read -r -d '' patch; do
    git -C "${context}" apply --check "${FACTORY_WORKSPACE}/${patch}"
    git -C "${context}" apply "${FACTORY_WORKSPACE}/${patch}"
  done < <(find "${overlay}" -type f -name '*.patch' -print0 | sort -z)
fi
scripts/validate_context.py "${catalog}" "${context}"

# Single-platform copy of the public base; build_image.sh verifies the digest.
skopeo copy --retry-times 5 --override-os "${platform%%/*}" --override-arch "${platform##*/}" \
  "docker://${base_ref}" "oci-archive:${work}/base.oci.tar"
base_digest=$(skopeo inspect --format '{{.Digest}}' "oci-archive:${work}/base.oci.tar")

jq -n --arg revision "${revision}" --arg base "${base_ref}" --arg digest "${base_digest}" \
  '{schemaVersion:"1.0",localDevelopment:true,harness:true,
    source:{revision:$revision},
    resources:[{kind:"oci",source:("docker://"+$base),digest:$digest}]}' \
  >"${work}/resource-lock.json"
printf 'FACTORY_IMAGE=%q\nSOURCE_REVISION=%q\nBASE_REF=%q\nBASE_DIGEST=%q\n' \
  "${image}" "${revision}" "${base_ref}" "${base_digest}" >"${work}/build.env"
echo "harness prepare: ${image} @ ${revision} on ${base_ref} (${base_digest})"
