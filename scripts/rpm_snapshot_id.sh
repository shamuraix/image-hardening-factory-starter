#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
base_kind=$(yq -r '.build.base.kind' "${catalog}")
if [[ "${base_kind}" == catalog ]]; then
  family=$(yq -r '.build.base.image' "${catalog}")
else
  version=$(yq -r '.product.version' "${catalog}")
  family=$([[ "${version}" == 9.* ]] && echo ubi9-minimal || echo ubi10-minimal)
fi
case "${family}" in
  ubi9-minimal) printf '%s\n' "${RPM_SNAPSHOT_UBI9_ID:?RPM_SNAPSHOT_UBI9_ID is required}" ;;
  ubi10-minimal) printf '%s\n' "${RPM_SNAPSHOT_UBI10_ID:?RPM_SNAPSHOT_UBI10_ID is required}" ;;
  *) echo "unknown RPM snapshot family: ${family}" >&2; exit 2 ;;
esac
