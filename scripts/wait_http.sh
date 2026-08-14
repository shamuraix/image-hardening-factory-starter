#!/usr/bin/env bash
set -euo pipefail

url=${1:?URL is required}
timeout=${2:-300}
start=$SECONDS
until curl --fail --silent --max-time 5 "${url}" >/dev/null 2>&1; do
  if (( SECONDS - start >= timeout )); then
    echo "timed out waiting for ${url}" >&2
    curl --fail --silent --show-error --max-time 5 "${url}" >/dev/null || true
    exit 1
  fi
  sleep 5
done
