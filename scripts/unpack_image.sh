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

layout=$(mktemp -d)
trap 'rm -rf "${layout}"' EXIT
rm -rf "${bundle}"
skopeo copy "oci-archive:${archive}" "oci:${layout}:candidate" >/dev/null
umoci unpack --rootless --image "${layout}:candidate" "${bundle}"
[[ -d "${bundle}/rootfs" ]] || {
  echo "unpacked image rootfs is missing" >&2
  exit 1
}
printf '%s\n' "${bundle}/rootfs"
