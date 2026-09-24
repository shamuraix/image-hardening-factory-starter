#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
# shellcheck disable=SC1090,SC1091
source "${work_dir}/build.env"
if jq -e '.localDevelopment == true' "${work_dir}/resource-lock.json" >/dev/null; then
  local_development=true
else
  local_development=false
fi
if [[ ${local_development} != true ]]; then
  scripts/require_rootless.sh buildkit
  for override in FACTORY_RPM_BASE_URL FACTORY_RPM_UPSTREAM_UBI_BASE FACTORY_UBI_REPO_PREFIX; do
    [[ -z ${!override:-} ]] || { echo "${override} is development-only" >&2; exit 2; }
  done
fi

containerfile=$(yq -r '.source.containerfile' "${catalog}")
platform=$(yq -r '.build.platforms[0]' "${catalog}")
created=$(git -C "${work_dir}/context" show -s --format=%cI "${SOURCE_REVISION}")
source_epoch=$(date --date="${created}" +%s)
build_id=${FACTORY_BUILD_ID:-local}
[[ "${build_id}" =~ ^[A-Za-z0-9_.-]+$ ]] || {
  echo "FACTORY_BUILD_ID contains characters that are invalid in an OCI tag" >&2
  exit 2
}
local_image_namespace=${FACTORY_LOCAL_IMAGE_NAMESPACE:-localhost/factory}
local_ref="${local_image_namespace}/${FACTORY_IMAGE}:${build_id}"
build_network=${FACTORY_BUILD_NETWORK:-default}
case "${build_network}" in
  default|none|host) ;;
  *) echo "FACTORY_BUILD_NETWORK must be one of: default, none, host" >&2; exit 2 ;;
esac
export FACTORY_BUILD_NETWORK="${build_network}"
skopeo_copy_args=(--retry-times 5)
if [[ ${FACTORY_REMOVE_TRANSPORT_SIGNATURES:-false} == true ]]; then
  skopeo_copy_args+=(--remove-signatures)
fi
work_abs=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${work_dir}")
context_abs=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${work_dir}/context")
private_parent_abs=
if [[ -n ${FACTORY_PRIVATE_TMPDIR:-} ]]; then
  private_parent_abs=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${FACTORY_PRIVATE_TMPDIR}")
  case "${private_parent_abs}/" in
    "${work_abs}/"*|"${context_abs}/"*)
      echo "FACTORY_PRIVATE_TMPDIR must not be inside the build work directory or context" >&2
      exit 2
      ;;
  esac
  mkdir -p "${private_parent_abs}"
  chmod 700 "${private_parent_abs}"
  private_dir=$(mktemp -d "${private_parent_abs}/build-image.XXXXXX")
else
  private_dir=$(mktemp -d /tmp/factory-build-inputs.XXXXXX)
fi
chmod 700 "${private_dir}"
cleanup_private() {
  rm -rf "${private_dir}"
  if [[ -n ${private_parent_abs} ]]; then
    rmdir "${private_parent_abs}" >/dev/null 2>&1 || true
  fi
}
cleanup_private_and_exit() {
  local status=${1:?status is required}
  trap - EXIT INT TERM
  cleanup_private
  exit "${status}"
}
trap cleanup_private EXIT
trap 'cleanup_private_and_exit 130' INT
trap 'cleanup_private_and_exit 143' TERM

if [[ ! -f "${work_dir}/base.oci.tar" ]]; then
  [[ "${BASE_REF}" == "${ARTIFACTORY_REGISTRY:?}/"*@sha256:* ]] || {
    echo "remote base must be digest-pinned in the internal registry" >&2; exit 2;
  }
  source scripts/lib/registry_auth.sh
  factory_registry_auth "${private_dir}/auth"
  authfile="${private_dir}/auth/config.json"
  printf '%s' "${ARTIFACTORY_READ_TOKEN:?}" | skopeo login --authfile "${authfile}" \
    --username oidc --password-stdin "${ARTIFACTORY_REGISTRY}"
  skopeo copy --authfile "${authfile}" --all --preserve-digests "${skopeo_copy_args[@]}" \
    "docker://${BASE_REF}" "oci-archive:${work_dir}/base.oci.tar"
  rm -f "${authfile}"
fi
if [[ -f "${work_dir}/base.oci.tar" ]]; then
  observed_base=$(skopeo inspect "oci-archive:${work_dir}/base.oci.tar" | jq -er '.Digest')
  [[ "${observed_base}" == "${BASE_DIGEST}" ]] || {
    echo "base archive digest does not match the selected base" >&2; exit 1;
  }
else
  echo "base OCI archive was not acquired" >&2
  exit 1
fi
base_layout="${private_dir}/base-layout"
dockerfile_dir="${private_dir}/dockerfile"
source_policy="${private_dir}/source-policy.json"
mkdir -p "${dockerfile_dir}"
mkdir -p "${base_layout}"
cat >"${source_policy}" <<'EOF'
{
  "rules": [
    {"action": "DENY", "selector": {"identifier": "docker-image://*"}},
    {"action": "DENY", "selector": {"identifier": "http://*"}},
    {"action": "DENY", "selector": {"identifier": "https://*"}},
    {"action": "DENY", "selector": {"identifier": "git://*"}},
    {"action": "DENY", "selector": {"identifier": "ssh://*"}}
  ]
}
EOF
skopeo copy --all --preserve-digests "${skopeo_copy_args[@]}" \
  "oci-archive:${work_dir}/base.oci.tar" "oci:${base_layout}:factory-base"
base_layout_abs=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${base_layout}")

args=(
  --frontend dockerfile.v0
  --local "context=${work_dir}/context"
  --local "dockerfile=${dockerfile_dir}"
  --opt "filename=Dockerfile"
  --oci-layout "base=${base_layout_abs}"
  --opt "context:factory-base=oci-layout://base@${observed_base}"
  --opt "build-arg:BASE_REF=factory-base"
  --opt "build-arg:SOURCE_DATE_EPOCH=${source_epoch}"
  --opt "platform=${platform}"
  --opt "label:org.opencontainers.image.revision=${SOURCE_REVISION}"
  --opt "label:org.opencontainers.image.created=${created}"
  --opt "label:org.opencontainers.image.source=${FACTORY_SOURCE_URL:-local}"
  --source-policy-file "${source_policy}"
  --no-cache
)
if [[ "${build_network}" != default ]]; then
  args+=(--opt "force-network-mode=${build_network}")
fi
if [[ "${build_network}" == host ]]; then
  args+=(--allow network.host)
fi

if [[ -n ${FACTORY_BASE_MAJOR:-} ]]; then
  base_major=${FACTORY_BASE_MAJOR}
elif [[ $(yq -r '.build.base.kind' "${catalog}") == catalog ]]; then
  base_catalog="$(dirname "${catalog}")/$(yq -r '.build.base.image' "${catalog}").yaml"
  base_major=$(yq -r '.product.version | split(".")[0]' "${base_catalog}")
else
  base_major=$(yq -r '.product.version | split(".")[0]' "${catalog}")
fi
args+=(--opt "build-arg:BASE_MAJOR=${base_major}")

repo_file="${private_dir}/factory.repo"
RPM_SOURCE_IDENTIFIER=$(scripts/write_repo_config.sh "${catalog}" "${repo_file}")
export RPM_SOURCE_IDENTIFIER
chmod 600 "${repo_file}"
args+=(--secret "id=factory-repo,src=${repo_file}")

while IFS=$'\t' read -r key value; do
  case "${key}" in
    BASE_REF|BASE_MAJOR|SOURCE_DATE_EPOCH|BUILDKIT_SYNTAX)
      echo "catalog build arg ${key} is reserved by the BuildKit migration" >&2
      exit 2
      ;;
  esac
  args+=(--opt "build-arg:${key}=${value}")
done < <(yq -r '.build.buildArgs // {} | to_entries[] | [.key,.value] | @tsv' "${catalog}")

PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}" python3 -m factory.buildkit adapt-dockerfile \
  "${work_dir}/context/${containerfile}" >"${dockerfile_dir}/Dockerfile"
if [[ -f "${work_dir}/context/${containerfile}.dockerignore" ]]; then
  cp "${work_dir}/context/${containerfile}.dockerignore" "${dockerfile_dir}/Dockerfile.dockerignore"
fi
build_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
buildkit_archive="${private_dir}/image.buildkit.oci.tar"
args+=(--output "type=oci,dest=${buildkit_archive},rewrite-timestamp=true")
SOURCE_DATE_EPOCH="${source_epoch}" scripts/run_buildkit.sh build "${args[@]}"
# Preserve the exact local reference in the archive handed to scanners and
# importers. The normalization copy is local and does not use a container store.
skopeo copy --all "${skopeo_copy_args[@]}" \
  "oci-archive:${buildkit_archive}" "oci-archive:${work_dir}/image.oci.tar:${local_ref}"
# The archive is the candidate handed to every scanner and importer, so record
# its final manifest digest rather than assuming exporter normalization
# preserved the BuildKit manifest byte-for-byte.
digest=$(skopeo inspect "oci-archive:${work_dir}/image.oci.tar:${local_ref}" | jq -er '.Digest')

jq -n \
  --arg image "${FACTORY_IMAGE}" \
  --arg digest "${digest}" \
  --arg sourceRevision "${SOURCE_REVISION}" \
  --arg baseRef "${BASE_REF}" \
  --arg baseDigest "${BASE_DIGEST}" \
  --arg factoryRevision "${FACTORY_COMMIT_SHA:-local}" \
  --arg rpmSource "${RPM_SOURCE_IDENTIFIER}" \
  --arg created "${created}" \
  --arg startedOn "${build_started}" \
  --arg finishedOn "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg builder "${FACTORY_RUNNER_ID:-local}" \
  '{startedOn:$startedOn,finishedOn:$finishedOn,builder:$builder,image:$image,digest:$digest,sourceRevision:$sourceRevision,baseRef:$baseRef,baseDigest:$baseDigest,factoryRevision:$factoryRevision,rpmSource:$rpmSource,created:$created}' \
  >"${work_dir}/image-metadata.json"
printf 'IMAGE_DIGEST=%s\nLOCAL_IMAGE_REF=%s\n' "${digest}" "${local_ref}" >>"${work_dir}/build.env"
