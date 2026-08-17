#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
profile=$(yq -r '.build.testProfile' "${catalog}")
output="${work_dir}/evidence/tests"
mkdir -p "${output}"

scripts/require_rootless.sh podman
podman load -i "${work_dir}/image.oci.tar" >"${output}/load.log"
# shellcheck disable=SC1090,SC1091
source "${work_dir}/build.env"
: "${LOCAL_IMAGE_REF:?LOCAL_IMAGE_REF is missing from build.env}"
podman image exists "${LOCAL_IMAGE_REF}" || {
  echo "loaded archive does not contain expected image ${LOCAL_IMAGE_REF}" >&2
  exit 2
}
image=${LOCAL_IMAGE_REF}
export FACTORY_TEST_IMAGE="${image}"
export FACTORY_TEST_OUTPUT="${output}"

set +e
"tests/profiles/${profile}/test.sh" >"${output}/test.log" 2>&1
status=$?
set -e
jq -n --argjson passed "$([[ ${status} -eq 0 ]] && echo true || echo false)" \
  --argjson exitCode "${status}" '{passed:$passed,exitCode:$exitCode}' >"${output}/result.json"
cat "${output}/test.log"
exit "${status}"
