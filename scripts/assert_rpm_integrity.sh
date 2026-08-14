#!/usr/bin/env bash
set -euo pipefail

image=${1:?image reference is required}
output=${2:?output path is required}
shift 2
[[ $# -gt 0 ]] || { echo "at least one RPM allowlist is required" >&2; exit 2; }
allowlists=("$@")
raw=$(mktemp)
trap 'rm -f "${raw}"' EXIT

# Timestamps are normalized for reproducible OCI layers and are not content
# integrity signals. Keep all other RPM verification checks enabled.
podman run --rm --user 0 --entrypoint /bin/bash "${image}" -c 'rpm -Va --nomtime || true' >"${raw}"
cp "${raw}" "${output}"

if [[ ! -s "${raw}" ]]; then
  exit 0
fi

unexpected=$(mktemp)
trap 'rm -f "${raw}" "${unexpected}"' EXIT
while IFS= read -r line; do
  path=${line##* }
  allowed=false
  for allowlist in "${allowlists[@]}"; do
    if grep --fixed-strings --line-regexp --quiet "${path}" "${allowlist}"; then
      allowed=true
      break
    fi
  done
  if [[ ${allowed} != true ]]; then
    printf '%s\n' "${line}" >>"${unexpected}"
  fi
done <"${raw}"

if [[ -s "${unexpected}" ]]; then
  echo "unexpected RPM verification deviations:" >&2
  cat "${unexpected}" >&2
  exit 1
fi
