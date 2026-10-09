#!/usr/bin/env bash
set -euo pipefail

image=${1:?image reference is required}
output=${2:?output path is required}
shift 2
[[ $# -gt 0 ]] || { echo "at least one RPM allowlist is required" >&2; exit 2; }
allowlists=("$@")
raw=$(mktemp)
errors=$(mktemp)
trap 'rm -f "${raw}" "${errors}"' EXIT

# Ghost entries (such as host-mounted /proc and /sys) have no RPM payload.
# Timestamps are normalized for reproducible OCI layers and are not content
# integrity signals. Root inside rootless Podman maps to the unprivileged
# factory user on the Kubernetes node.
scripts/require_rootless.sh podman
# Run the candidate on its declared platform (FACTORY_TEST_PLATFORM, from the
# catalog) so Podman selects it without the "image platform does not match"
# warning that an emulated build prints on a foreign node.
platform_args=()
[[ -n ${FACTORY_TEST_PLATFORM:-} ]] && platform_args=(--platform "${FACTORY_TEST_PLATFORM}")
status=0
podman run --rm --network none "${platform_args[@]}" --cgroups=disabled --user 0 --entrypoint /bin/bash "${image}" \
  -c 'rpm -q rpm >/dev/null || exit 2; rpm -Va --nomtime --noghost' >"${raw}" 2>"${errors}" || status=$?
cat "${raw}" "${errors}" >"${output}"
# Anything rpm or Podman reports as an error means the verification cannot be
# trusted. Podman's own WARNING lines are kept in the evidence but are not
# errors.
fatal=$(grep -v '^WARNING: ' "${errors}" || true)
if [[ ${status} -gt 1 || -n ${fatal} ]] || { [[ ${status} -ne 0 && ! -s ${raw} ]]; }; then
  echo "RPM verification could not complete (exit ${status})" >&2
  cat "${errors}" >&2
  exit 1
fi

if [[ ! -s "${raw}" ]]; then
  exit 0
fi

unexpected=$(mktemp)
trap 'rm -f "${raw}" "${unexpected}" "${errors}"' EXIT
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
