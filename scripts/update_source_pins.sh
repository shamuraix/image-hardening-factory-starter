#!/usr/bin/env bash
set -euo pipefail

catalog_directory=${FACTORY_CATALOG_DIR:-catalog/images}
upstream_branch=${FACTORY_UPSTREAM_BRANCH:?FACTORY_UPSTREAM_BRANCH is required}
vendir_config=${FACTORY_VENDIR_CONFIG:-vendir/config.yml}

for catalog in "${catalog_directory}"/*.yaml; do
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
    "${vendir_config}" >/dev/null
  REVISION="${revision}" yq -i '.source.revision = strenv(REVISION)' "${catalog}"
  IMAGE_NAME="${image}" REVISION="${revision}" yq -i \
    '(.directories[].contents[] | select(.path == strenv(IMAGE_NAME)).git.ref) = strenv(REVISION)' \
    "${vendir_config}"
  printf '%s %s\n' "${image}" "${revision}"
done
