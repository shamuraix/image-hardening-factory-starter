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
    ;;
  *)
    echo "unsupported container tool: ${tool}" >&2
    exit 2
    ;;
esac
