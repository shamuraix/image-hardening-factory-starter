#!/usr/bin/env bash
set -euo pipefail

image=${1:?usage: scripts/local_build.sh IMAGE}
catalog="catalog/images/${image}.yaml"
[[ -f "${catalog}" ]] || { echo "unknown image: ${image}" >&2; exit 2; }

if [[ ${LOCAL_USE_UPSTREAM_UBI_REPOS:-false} == true ]]; then
  echo "LOCAL_USE_UPSTREAM_UBI_REPOS is no longer supported; use the internal Artifactory UBI cache" >&2
  exit 2
fi
if [[ -n ${LOCAL_RPM_REPO_DIR:-} && ! -d ${LOCAL_RPM_REPO_DIR}/repodata ]]; then
  echo "${LOCAL_RPM_REPO_DIR}/repodata is missing" >&2
  exit 2
fi
for command in git curl python3 podman buildah skopeo yq jq; do
  command -v "${command}" >/dev/null || { echo "required command is missing: ${command}" >&2; exit 2; }
done
scripts/require_rootless.sh podman
scripts/require_rootless.sh buildah

local_root=${LOCAL_FACTORY_ROOT:-.local-factory}
registry=${LOCAL_REGISTRY:-127.0.0.1:5000}
registry_image=${LOCAL_REGISTRY_IMAGE:-docker.io/library/registry:2}
registry_container=${LOCAL_REGISTRY_CONTAINER:-factory-local-registry}
registry_namespace=${LOCAL_REGISTRY_NAMESPACE:-factory}
local_image_namespace=${LOCAL_IMAGE_NAMESPACE:-localhost/factory}
rpm_port=${LOCAL_RPM_PORT:-18080}
work_dir="work/${image}"
context="${work_dir}/context"
cache="${work_dir}/local-cache"
revision=$(yq -r '.source.revision' "${catalog}")
overlay=$(yq -r '.source.overlay // ""' "${catalog}")
snapshot_id=${LOCAL_RPM_SNAPSHOT_ID:-local-dev}
base_kind=$(yq -r '.build.base.kind' "${catalog}")
if [[ ${base_kind} == catalog ]]; then
  catalog_base_image=$(yq -r '.build.base.image' "${catalog}")
  base_image=${LOCAL_BASE_IMAGE_OVERRIDE:-${catalog_base_image}}
  [[ -f "catalog/images/${base_image}.yaml" ]] || {
    echo "unknown LOCAL_BASE_IMAGE_OVERRIDE: ${base_image}" >&2
    exit 2
  }
  [[ $(yq -r '.build.base.kind' "catalog/images/${base_image}.yaml") == upstream ]] || {
    echo "LOCAL_BASE_IMAGE_OVERRIDE must name a catalog base image" >&2
    exit 2
  }
  rpm_catalog="catalog/images/${base_image}.yaml"
else
  if [[ -n ${LOCAL_BASE_IMAGE_OVERRIDE:-} ]]; then
    echo "LOCAL_BASE_IMAGE_OVERRIDE is valid only for application images" >&2
    exit 2
  fi
  catalog_base_image=
  base_image=
  rpm_catalog="${catalog}"
fi
rpm_major=$(yq -r '.product.version | split(".")[0]' "${rpm_catalog}")

mkdir -p "${local_root}" "${work_dir}" "${cache}"

case "${registry}" in
  127.0.0.1:*|localhost:*) ;;
  *) echo "LOCAL_REGISTRY must be a loopback host and port" >&2; exit 2 ;;
esac

# Build catalog bases first. Each invocation owns its loopback services, which
# avoids port conflicts and also makes a single application target sufficient.
if [[ ${base_kind} == catalog ]]; then
  if [[ ! -s "work/${base_image}/image.oci.tar" ]]; then
    env -u LOCAL_SOURCE_DIR -u LOCAL_BASE_IMAGE_OVERRIDE "${BASH_SOURCE[0]}" "${base_image}"
  fi
fi

started_registry=false
if ! curl --fail --silent "http://${registry}/v2/" >/dev/null 2>&1; then
  podman run --detach --rm --cgroups=disabled --name "${registry_container}" \
    --publish "127.0.0.1:${registry##*:}:5000" "${registry_image}" >/dev/null
  started_registry=true
fi

rpm_server_pid=
rpm_base_url=
if [[ -n ${LOCAL_RPM_REPO_DIR:-} ]]; then
  python3 -m http.server "${rpm_port}" --bind 127.0.0.1 \
    --directory "${LOCAL_RPM_REPO_DIR}" >"${local_root}/rpm-http.log" 2>&1 &
  rpm_server_pid=$!
  rpm_base_url="http://127.0.0.1:${rpm_port}"
fi
cleanup_local_services() {
  if [[ -n ${rpm_server_pid} ]]; then
    kill "${rpm_server_pid}" >/dev/null 2>&1 || true
    wait "${rpm_server_pid}" >/dev/null 2>&1 || true
  fi
  if [[ ${started_registry} == true && ${LOCAL_KEEP_REGISTRY:-false} != true ]]; then
    podman stop "${registry_container}" >/dev/null 2>&1 || true
  fi
}
trap cleanup_local_services EXIT

curl_args=(--fail --silent --show-error --location)
if [[ -n ${LOCAL_CA_BUNDLE:-} ]]; then
  [[ -r ${LOCAL_CA_BUNDLE} ]] || {
    echo "LOCAL_CA_BUNDLE is not readable: ${LOCAL_CA_BUNDLE}" >&2
    exit 2
  }
  curl_args+=(--cacert "${LOCAL_CA_BUNDLE}")
fi
if [[ -z ${LOCAL_RPM_REPO_DIR:-} ]]; then
  : "${ARTIFACTORY_URL:?ARTIFACTORY_URL is required when LOCAL_RPM_REPO_DIR is not set}"
  : "${ARTIFACTORY_READ_TOKEN:?ARTIFACTORY_READ_TOKEN is required when LOCAL_RPM_REPO_DIR is not set}"
  major=${rpm_major}
  arch=$(yq -r '.build.platforms[0] | split("/")[1]' "${catalog}")
  [[ ${arch} == amd64 ]] && rpm_arch=x86_64 || rpm_arch=${arch}
  rpm_cache_repository=${LOCAL_RPM_CACHE_REPOSITORY:?LOCAL_RPM_CACHE_REPOSITORY is required when LOCAL_RPM_REPO_DIR is not set}
  [[ ${rpm_cache_repository} =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "LOCAL_RPM_CACHE_REPOSITORY must be an Artifactory repository name" >&2
    exit 2
  }
  artifactory_repository_url="${ARTIFACTORY_URL%/}/artifactory/${rpm_cache_repository}"
  rpm_cache_ubi_root_url=${LOCAL_RPM_CACHE_UBI_ROOT_URL:-${artifactory_repository_url}/content/public/ubi}
  case "${rpm_cache_ubi_root_url}" in
    "${ARTIFACTORY_URL%/}/artifactory/"*) ;;
    *) echo "LOCAL_RPM_CACHE_UBI_ROOT_URL must point inside ARTIFACTORY_URL" >&2; exit 2 ;;
  esac
  rpm_cache_prefix="${rpm_cache_ubi_root_url%/}/dist/ubi${major}/${major}/${rpm_arch}"
  curl_args+=(--header "Authorization: Bearer ${ARTIFACTORY_READ_TOKEN}")
  repomd_file="${local_root}/${image}-cache-repomd.sha256"
  : >"${repomd_file}"
  for channel in baseos appstream; do
    metadata="${local_root}/${image}-${channel}-repomd.xml"
    curl "${curl_args[@]}" --output "${metadata}" \
      "${rpm_cache_prefix}/${channel}/os/repodata/repomd.xml"
    sha256sum "${metadata}" >>"${repomd_file}"
  done
  rpm_source="artifactory:${rpm_cache_repository}:ubi${major}:${rpm_arch}"
else
  repomd_file="${local_root}/${image}-repomd.xml"
  for _ in $(seq 1 30); do
    curl "${curl_args[@]}" --output "${repomd_file}" \
      "${rpm_base_url%/}/repodata/repomd.xml" >/dev/null 2>&1 && break
    sleep 0.2
  done
  curl "${curl_args[@]}" --output "${repomd_file}" \
    "${rpm_base_url%/}/repodata/repomd.xml"
  rpm_source="${rpm_base_url}"
fi

rm -rf "${context}"
source_dir=${LOCAL_SOURCE_DIR:-}
if [[ -z ${source_dir} && -n ${LOCAL_SOURCE_ROOT:-} && -d ${LOCAL_SOURCE_ROOT}/${image} ]]; then
  source_dir="${LOCAL_SOURCE_ROOT}/${image}"
fi
if [[ -n ${source_dir} ]]; then
  git clone --no-local --no-checkout "${source_dir}" "${context}"
else
  upstream=$(yq -r '.source.upstream' "${catalog}")
  git clone --filter=blob:none --no-checkout "${upstream}" "${context}"
fi
git -C "${context}" checkout --detach "${revision}"
[[ $(git -C "${context}" rev-parse HEAD) == "${revision}" ]]

if [[ -n ${overlay} ]]; then
  while IFS= read -r -d '' patch; do
    git -C "${context}" apply --check "${PWD}/${patch}"
    git -C "${context}" apply "${PWD}/${patch}"
  done < <(find "${overlay}" -type f -name '*.patch' -print0 | sort -z)
fi
local_ca_cert=${LOCAL_CA_CERT:-${LOCAL_CA_BUNDLE:-}}
if [[ -n ${local_ca_cert} && ${image} == ubi9-minimal ]]; then
  [[ -r ${local_ca_cert} ]] || {
    echo "local CA file is not readable: ${local_ca_cert}" >&2
    exit 2
  }
  install -m 0644 "${local_ca_cert}" "${context}/certs/factory-local-ca.crt"
fi
scripts/validate_context.py "${catalog}" "${context}"

PYTHONPATH=. python3 -m factory.cli intake --image "${catalog}" --source-dir "${context}" \
  --cache-dir "${cache}" --output "${work_dir}/resource-lock.json"
while IFS=$'\t' read -r filename; do
  [[ -n ${filename} ]] && cp "${cache}/${filename}" "${context}/${filename}"
done < <(jq -r '.resources[] | select(.kind == "file") | [.filename] | @tsv' "${work_dir}/resource-lock.json")

repomd_digest="sha256:$(sha256sum "${repomd_file}" | awk '{print $1}')"
if [[ ${base_kind} == catalog ]]; then
 base_ref="${local_image_namespace}/${base_image}:local"
  cp "work/${base_image}/image.oci.tar" "${work_dir}/base.oci.tar"
  base_digest=$(jq -er '.digest' "work/${base_image}/image-metadata.json")
else
  base_source=$(jq -er '.resources[] | select(.kind == "oci") | .source' \
    "${work_dir}/resource-lock.json" | head -n1)
  base_ref="${local_image_namespace}/${image}-upstream:local"
  skopeo copy --retry-times 5 --remove-signatures --all "${base_source}" \
    "docker://${registry}/${registry_namespace}/${image}-upstream:local" \
    --dest-tls-verify=false
  skopeo copy --retry-times 5 --remove-signatures --all \
    "docker://${registry}/${registry_namespace}/${image}-upstream:local" \
    "oci-archive:${work_dir}/base.oci.tar:${base_ref}" --src-tls-verify=false
  base_digest=$(skopeo inspect --tls-verify=false \
    "docker://${registry}/${registry_namespace}/${image}-upstream:local" | jq -er '.Digest')
fi

jq --arg snapshot "${snapshot_id}" --arg repomdDigest "${repomd_digest}" \
  --arg baseRef "${base_ref}" --arg baseDigest "${base_digest}" \
  --arg rpmSource "${rpm_source}" --arg catalogBase "${catalog_base_image}" \
  --arg effectiveBase "${base_image}" \
  '. + {localDevelopment:true,rpmSnapshot:{snapshotId:$snapshot,repomdDigest:$repomdDigest,developmentSource:$rpmSource},baseImage:{reference:$baseRef,digest:$baseDigest}} +
    (if $catalogBase != "" and $catalogBase != $effectiveBase then
      {developmentBaseOverride:{catalogBase:$catalogBase,effectiveBase:$effectiveBase}}
    else {} end)' \
  "${work_dir}/resource-lock.json" >"${work_dir}/resource-lock.tmp.json"
mv "${work_dir}/resource-lock.tmp.json" "${work_dir}/resource-lock.json"

cat >"${work_dir}/build.env" <<EOF
FACTORY_IMAGE=${image}
SOURCE_REVISION=${revision}
BASE_REF=${base_ref}
BASE_DIGEST=${base_digest}
RPM_REPOMD_DIGEST=${repomd_digest}
EOF

export FACTORY_IMAGE="${image}"
export FACTORY_RPM_BASE_URL="${rpm_base_url}"
if [[ -z ${LOCAL_RPM_REPO_DIR:-} ]]; then
  export FACTORY_UBI_REPO_PREFIX="${rpm_cache_prefix}"
  export FACTORY_RPM_REPO_USERNAME=${LOCAL_ARTIFACTORY_USERNAME:-oidc}
  export FACTORY_RPM_REPO_PASSWORD="${ARTIFACTORY_READ_TOKEN}"
else
  unset FACTORY_UBI_REPO_PREFIX FACTORY_RPM_REPO_USERNAME FACTORY_RPM_REPO_PASSWORD
fi
export FACTORY_BUILD_NETWORK=host
export FACTORY_BASE_MAJOR="${rpm_major}"
export FACTORY_RPM_GPGCHECK=${LOCAL_RPM_GPGCHECK:-1}
export FACTORY_RPM_REPO_GPGCHECK=${LOCAL_RPM_REPO_GPGCHECK:-1}
export FACTORY_RPM_SSLVERIFY=${LOCAL_RPM_SSLVERIFY:-1}
# The production helper derives its variable name from the unchanged catalog
# dependency. A local base override may intentionally use the other family, so
# define both development labels; repository selection above still follows the
# effective base and only the matching UBI repositories are enabled.
export RPM_SNAPSHOT_UBI9_ID="${snapshot_id}"
export RPM_SNAPSHOT_UBI10_ID="${snapshot_id}"
export FACTORY_REMOVE_TRANSPORT_SIGNATURES=true

scripts/build_image.sh "${catalog}" "${work_dir}"
skopeo copy --retry-times 5 --remove-signatures "oci-archive:${work_dir}/image.oci.tar" \
  "docker://${registry}/${registry_namespace}/${image}:local" --dest-tls-verify=false

printf 'Built %s\nOCI archive: %s/image.oci.tar\nLocal registry: %s/%s/%s:local\nDevelopment lock: %s/resource-lock.json\n' \
  "${image}" "${work_dir}" "${registry}" "${registry_namespace}" "${image}" "${work_dir}"
