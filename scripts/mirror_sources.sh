#!/usr/bin/env bash
set -euo pipefail

: "${INTERNAL_GIT_BASE_URL:?}"
: "${GITLAB_MIRROR_TOKEN:?}"

for catalog in catalog/images/*.yaml; do
  upstream=$(yq -r '.source.upstream' "${catalog}")
  mirror_path=$(yq -r '.source.mirrorPath' "${catalog}")
  revision=$(yq -r '.source.revision' "${catalog}")
  directory=$(mktemp -d)
  trap 'rm -rf "${directory}"' EXIT
  git clone --mirror "${upstream}" "${directory}/source.git"
  git -C "${directory}/source.git" cat-file -e "${revision}^{commit}"
  target="https://oauth2:${GITLAB_MIRROR_TOKEN}@${CI_SERVER_HOST}/${mirror_path}.git"
  git -C "${directory}/source.git" push --mirror "${target}"
  rm -rf "${directory}"
  trap - EXIT
done
