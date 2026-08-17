#!/usr/bin/env bash
set -euo pipefail

tool=${1:?container tool is required}
case "${tool}" in
  buildah)
    rootless=$(buildah info --format '{{.host.rootless}}')
    ;;
  podman)
    rootless=$(podman info --format '{{.Host.Security.Rootless}}')
    ;;
  *)
    echo "unsupported container tool: ${tool}" >&2
    exit 2
    ;;
esac

[[ "${rootless}" == true ]] || {
  echo "${tool} must run rootless" >&2
  exit 1
}
