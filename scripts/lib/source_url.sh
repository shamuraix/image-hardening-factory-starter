#!/usr/bin/env bash
# factory_source_url CATALOG: the git URL the factory clones the pinned Iron Bank
# source from. Default: the internal mirror (INTERNAL_GIT_BASE_URL + mirrorPath).
# FACTORY_SOURCE_MODE=upstream reads source.upstream directly, for environments
# without a mirror. Either way the clone is checked out at the catalog's full
# commit SHA and the checkout is verified against it, so a different server
# cannot substitute different content.
factory_source_url() {
  local catalog=${1:?catalog file is required}
  case "${FACTORY_SOURCE_MODE:-mirror}" in
    mirror)
      : "${INTERNAL_GIT_BASE_URL:?INTERNAL_GIT_BASE_URL is required}"
      printf '%s/%s.git' "${INTERNAL_GIT_BASE_URL%/}" "$(yq -r '.source.mirrorPath' "${catalog}")"
      ;;
    upstream) yq -r '.source.upstream' "${catalog}" ;;
    *) echo "unsupported FACTORY_SOURCE_MODE: ${FACTORY_SOURCE_MODE}" >&2; return 2 ;;
  esac
}
