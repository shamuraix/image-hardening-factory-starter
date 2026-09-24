#!/usr/bin/env bash
set -euo pipefail

catalog_directory=${FACTORY_CATALOG_DIR:-catalog/images}
upstream_branch=${FACTORY_UPSTREAM_BRANCH:?FACTORY_UPSTREAM_BRANCH is required}
vendir_config=${FACTORY_VENDIR_CONFIG:-vendir/config.yml}
vendir_sync=${FACTORY_VENDIR_SYNC:-true}

for tool in git yq awk; do
  command -v "${tool}" >/dev/null || { echo "required command is missing: ${tool}" >&2; exit 2; }
done
case "${vendir_sync}" in
  true|false) ;;
  *) echo "FACTORY_VENDIR_SYNC must be true or false" >&2; exit 2 ;;
esac
if [[ "${vendir_sync}" == true ]]; then
  command -v vendir >/dev/null || { echo "required command is missing: vendir" >&2; exit 2; }
fi

shopt -s nullglob
catalogs=("${catalog_directory}"/*.yaml)
(( ${#catalogs[@]} > 0 )) || { echo "no catalog files found in ${catalog_directory}" >&2; exit 2; }
updated=0
for catalog in "${catalogs[@]}"; do
  image=$(yq -er '.metadata.name' "${catalog}")
  upstream=$(yq -er '.source.upstream' "${catalog}")
  revision=$(
    git ls-remote --exit-code "${upstream}" "refs/heads/${upstream_branch}" |
      awk 'NR == 1 {print $1}'
  )
  [[ "${revision}" =~ ^[0-9a-f]{40}$ ]] || {
    echo "unable to resolve ${upstream_branch} for ${image}" >&2
    exit 1
  }
  IMAGE_NAME="${image}" yq -e \
    '.directories[].contents[] | select(.path == strenv(IMAGE_NAME)) | .git.ref' \
    "${vendir_config}" >/dev/null || {
    echo "vendir config is missing image path: ${image}" >&2
    exit 1
  }
  REVISION="${revision}" yq -i '.source.revision = strenv(REVISION)' "${catalog}"
  IMAGE_NAME="${image}" REVISION="${revision}" yq -i \
    '(.directories[].contents[] | select(.path == strenv(IMAGE_NAME)).git.ref) = strenv(REVISION)' \
    "${vendir_config}"
  updated=1
  printf '%s %s\n' "${image}" "${revision}"
done
if [[ "${vendir_sync}" == true && "${updated}" == 1 ]]; then
  vendir sync --file "${vendir_config}"
fi
