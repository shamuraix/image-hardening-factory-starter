#!/usr/bin/env bash
set -euo pipefail

: "${FACTORY_TEST_IMAGE:?}"
: "${FACTORY_TEST_OUTPUT:?}"
inspect=$(podman image inspect "${FACTORY_TEST_IMAGE}")
[[ $(jq -r '.[0].Config.User' <<<"${inspect}") == confluence ]]
jq -e '.[0].Config.ExposedPorts | has("8090/tcp") and has("8091/tcp")' <<<"${inspect}" >/dev/null
podman run --rm --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" -c '
  set -e
  java -version
  test -x /usr/bin/tini
  test -x /entrypoint.py
  test "$(id -u)" = 2002
'
base_major=$(podman run --rm --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" \
  -c '. /etc/os-release; printf "%s" "${VERSION_ID%%.*}"')
allowlists=(tests/profiles/base/rpm-verify.allow tests/profiles/confluence/rpm-verify.allow)
[[ ${base_major} == 10 ]] && allowlists+=(tests/profiles/base/rpm-verify.ubi10.allow)
scripts/assert_rpm_integrity.sh "${FACTORY_TEST_IMAGE}" \
  "${FACTORY_TEST_OUTPUT}/rpm-verify.txt" "${allowlists[@]}"

if [[ ${FACTORY_ENABLE_FULL_INTEGRATION:-true} == true ]]; then
  name="factory-confluence-${CI_JOB_ID:-local}"
  podman run --detach --name "${name}" --publish 127.0.0.1::8090 "${FACTORY_TEST_IMAGE}" >/dev/null
  trap 'podman logs "${name}" >"${FACTORY_TEST_OUTPUT}/container.log" 2>&1 || true; podman rm -f "${name}" >/dev/null 2>&1 || true' EXIT
  port=$(podman port "${name}" 8090/tcp | awk -F: 'NR==1{print $NF}')
  scripts/wait_http.sh "http://127.0.0.1:${port}/status" 600
  podman stop --time 60 "${name}" >/dev/null
fi
