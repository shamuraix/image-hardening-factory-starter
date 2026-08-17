#!/usr/bin/env bash
set -euo pipefail

archive=${1:?OCI archive is required}
bundle=${2:?bundle output directory is required}
[[ -s "${archive}" ]] || {
  echo "OCI archive is missing or empty: ${archive}" >&2
  exit 2
}
[[ "${bundle}" != / && "${bundle}" != . ]] || {
  echo "refusing unsafe bundle output directory: ${bundle}" >&2
  exit 2
}
scripts/require_rootless.sh podman

layout=$(mktemp -d)
unpacked=false
cleanup() {
  rm -rf "${layout}"
  if [[ "${unpacked}" != true ]]; then
    podman unshare rm -rf "${bundle}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
podman unshare rm -rf "${bundle}"
skopeo copy "oci-archive:${archive}" "oci:${layout}:candidate" >/dev/null
podman unshare umoci unpack --image "${layout}:candidate" "${bundle}"
[[ -d "${bundle}/rootfs" ]] || {
  echo "unpacked image rootfs is missing" >&2
  exit 1
}
unpacked=true
printf '%s\n' "${bundle}/rootfs"
