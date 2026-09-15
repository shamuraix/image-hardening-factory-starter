#!/usr/bin/env bash
set -euo pipefail

tool=${1:?container tool is required}
case "${tool}" in
  buildkit)
    [[ $(id -u) != 0 ]] || {
      echo "buildkit must run as a non-root user via scripts/run_buildkit.sh" >&2
      exit 1
    }
    command -v buildctl >/dev/null || {
      echo "required command is missing: buildctl" >&2
      exit 2
    }
    rootless=true
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
