#!/usr/bin/env bash
set -euo pipefail

: "${FACTORY_TEST_IMAGE:?}"
: "${FACTORY_TEST_OUTPUT:?}"
inspect=$(podman image inspect "${FACTORY_TEST_IMAGE}")
[[ $(jq -r '.[0].Config.User' <<<"${inspect}") == bitbucket ]]
jq -e '.[0].Config.ExposedPorts | has("7990/tcp") and has("7999/tcp")' <<<"${inspect}" >/dev/null
podman run --rm --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" -c '
  set -e
  java -version
  git --version | grep -E "git version 2\.49\."
  test -x /usr/bin/tini
  test -x /entrypoint.py
  test "$(id -u)" = 2003
'
base_major=$(podman run --rm --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" \
  -c '. /etc/os-release; printf "%s" "${VERSION_ID%%.*}"')
allowlists=(tests/profiles/base/rpm-verify.allow tests/profiles/bitbucket/rpm-verify.allow)
[[ ${base_major} == 10 ]] && allowlists+=(tests/profiles/base/rpm-verify.ubi10.allow)
scripts/assert_rpm_integrity.sh "${FACTORY_TEST_IMAGE}" \
  "${FACTORY_TEST_OUTPUT}/rpm-verify.txt" "${allowlists[@]}"

if [[ ${FACTORY_ENABLE_FULL_INTEGRATION:-true} == true ]]; then
  name="factory-bitbucket-${CI_JOB_ID:-local}"
  podman run --detach --name "${name}" --publish 127.0.0.1::7990 \
    --env ELASTICSEARCH_ENABLED=false "${FACTORY_TEST_IMAGE}" >/dev/null
  trap 'podman logs "${name}" >"${FACTORY_TEST_OUTPUT}/container.log" 2>&1 || true; podman rm -f "${name}" >/dev/null 2>&1 || true' EXIT
  port=$(podman port "${name}" 7990/tcp | awk -F: 'NR==1{print $NF}')
  scripts/wait_http.sh "http://127.0.0.1:${port}/status" 600
  podman stop --time 60 "${name}" >/dev/null
fi
