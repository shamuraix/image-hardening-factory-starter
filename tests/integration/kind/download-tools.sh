#!/usr/bin/env bash
set -euo pipefail
output=${1:?tool output directory}
architecture=${2:-$(uname -m)}
case "${architecture}" in x86_64) architecture=amd64 ;; aarch64) architecture=arm64 ;; esac
exec python3 "$(dirname "$0")/download-tools.py" "${output}" --arch "${architecture}"
