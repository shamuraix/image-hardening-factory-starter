#!/usr/bin/env bash
# Harness stand-in for the prepare stage (factory-prepare / prepare_context.sh),
# run by the real factory-stage Task. The harness has no internal git mirrors,
# Artifactory, or intake key, so this:
#   - clones the pinned Iron Bank revision from Repo One and applies the overlays
#     (exactly as prepare_context.sh does from the mirror);
#   - base images (build.base.kind: upstream): pulls the public UBI image that
#     matches the catalog version;
#   - application images (build.base.kind: catalog): verifies the seal of the
#     base built earlier in the same PipelineRun (HARNESS_BASE_SEAL) and uses
#     that archive, then downloads every hardening-manifest resource from its
#     declared URL and checks it against the manifest's checksum (the intake
#     stage's job in production).
# The resource lock it writes is unsigned and marked localDevelopment, which
# import_image.sh and sign_and_attest.sh refuse, so a harness candidate can
# never be imported, signed, or promoted.
set -euo pipefail
catalog=${FACTORY_CATALOG_FILE:?}
work=${FACTORY_WORK_DIR:?}
context="${work}/context"
local_image_namespace=${LOCAL_IMAGE_NAMESPACE:-localhost/factory}

image=$(yq -r '.metadata.name' "${catalog}")
upstream=$(yq -r '.source.upstream' "${catalog}")
revision=$(yq -r '.source.revision' "${catalog}")
overlay=$(yq -r '.source.overlay // ""' "${catalog}")
manifest_name=$(yq -r '.source.hardeningManifest // "hardening_manifest.yaml"' "${catalog}")
base_kind=$(yq -r '.build.base.kind' "${catalog}")

echo "harness prepare: cloning ${upstream} @ ${revision}"
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
echo "harness prepare: validating the build context"
scripts/validate_context.py "${catalog}" "${context}"

entries="${work}/resource-entries.jsonl"
: >"${entries}"
case "${base_kind}" in
  upstream)
    major=$(yq -r '.product.version | split(".")[0]' "${catalog}")
    version=$(yq -r '.product.version' "${catalog}")
    platform=$(yq -r '.build.platforms[0]' "${catalog}")
    base_ref="registry.access.redhat.com/ubi${major}/ubi-minimal:${version}"
    echo "harness prepare: pulling ${base_ref} for ${platform}"
    # Red Hat signs these images and an OCI archive cannot carry signatures, so
    # they are dropped; build_image.sh binds the archive to BASE_DIGEST.
    skopeo copy --retry-times 5 --remove-signatures \
      --override-os "${platform%%/*}" --override-arch "${platform##*/}" \
      "docker://${base_ref}" "oci-archive:${work}/base.oci.tar"
    base_digest=$(skopeo inspect --format '{{.Digest}}' "oci-archive:${work}/base.oci.tar")
    jq -nc --arg source "docker://${base_ref}" --arg digest "${base_digest}" \
      '{kind:"oci",source:$source,digest:$digest}' >>"${entries}"
    ;;
  catalog)
    base=$(yq -r '.build.base.image' "${catalog}")
    : "${HARNESS_BASE_SEAL:?application images need the base built in the same run (harness-build-app)}"
    echo "harness prepare: verifying the ${base} build from this run"
    # The base was sealed under its own work directory; verify it there before
    # trusting the archive, as every stage does with its inputs.
    FACTORY_WORK_DIR="work/${base}" scripts/tekton/artifacts.sh verify "build=${HARNESS_BASE_SEAL}"
    cp "work/${base}/image.oci.tar" "${work}/base.oci.tar"
    base_ref="${local_image_namespace}/${base}:dependency"
    base_digest=$(jq -er '.digest' "work/${base}/image-metadata.json")
    jq -nc --arg source "harness://work/${base}/image.oci.tar" --arg digest "${base_digest}" \
      '{kind:"oci",source:$source,digest:$digest}' >>"${entries}"

    manifest="${context}/${manifest_name}"
    [[ -f ${manifest} ]] || { echo "${manifest_name} is missing from the context" >&2; exit 1; }
    while IFS=$'\t' read -r url filename algorithm expected; do
      [[ -n ${url} ]] || continue
      case "${url}" in
        docker://*) continue ;;  # images come from the factory, not the manifest
        https://*) ;;
        *) echo "unsupported resource URL ${url}: only https:// is downloaded" >&2; exit 1 ;;
      esac
      # The filename lands in the build context; never let it leave it.
      [[ ${filename} =~ ^[A-Za-z0-9._+-]+$ && ${filename} != .* ]] || {
        echo "unsafe resource filename: ${filename}" >&2; exit 1;
      }
      case "${algorithm}" in
        sha256|sha512) ;;
        *) echo "resource ${filename} has validation type '${algorithm}'; need sha256 or sha512" >&2; exit 1 ;;
      esac
      [[ ${expected} =~ ^[0-9a-f]{64}([0-9a-f]{64})?$ ]] || {
        echo "resource ${filename} has a malformed ${algorithm} value" >&2; exit 1;
      }
      echo "harness prepare: fetching ${filename}"
      curl --fail --silent --show-error --location --retry 3 \
        --proto '=https' --proto-redir '=https' --output "${context}/${filename}" "${url}"
      printf '%s  %s\n' "${expected}" "${context}/${filename}" | "${algorithm}sum" --check --status || {
        echo "resource ${filename} does not match its ${algorithm} in ${manifest_name}" >&2
        rm -f "${context}/${filename}"
        exit 1
      }
      jq -nc --arg url "${url}" --arg filename "${filename}" --arg digest "${algorithm}:${expected}" \
        '{kind:"file",url:$url,filename:$filename,contentDigest:$digest}' >>"${entries}"
    done < <(yq -r '.resources // [] | .[] | [.url, .filename, (.validation.type // ""), (.validation.value // "")] | @tsv' "${manifest}")
    ;;
  *)
    echo "unknown build.base.kind ${base_kind}" >&2
    exit 2
    ;;
esac

jq -s --arg revision "${revision}" \
  '{schemaVersion:"1.0",localDevelopment:true,harness:true,source:{revision:$revision},resources:.}' \
  "${entries}" >"${work}/resource-lock.json"
rm -f "${entries}"
printf 'FACTORY_IMAGE=%q\nSOURCE_REVISION=%q\nBASE_REF=%q\nBASE_DIGEST=%q\n' \
  "${image}" "${revision}" "${base_ref}" "${base_digest}" >"${work}/build.env"
echo "harness prepare: ${image} @ ${revision} on ${base_ref} (${base_digest})"
