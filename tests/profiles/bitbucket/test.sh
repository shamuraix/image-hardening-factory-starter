#!/usr/bin/env bash
set -euo pipefail

: "${FACTORY_TEST_IMAGE:?}"
: "${FACTORY_TEST_OUTPUT:?}"
inspect=$(podman image inspect "${FACTORY_TEST_IMAGE}")
[[ $(jq -r '.[0].Config.User' <<<"${inspect}") == bitbucket ]]
jq -e '.[0].Config.ExposedPorts | has("7990/tcp") and has("7999/tcp")' <<<"${inspect}" >/dev/null
podman run --rm --network none --platform "${FACTORY_TEST_PLATFORM:?}" --cgroups=disabled --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" -c '
  set -e
  java -version
  git --version | grep -E "git version 2\.49\."
  test -x /usr/bin/tini
  test -x /entrypoint.py
  test "$(id -u)" = 2003
'
base_major=$(podman run --rm --network none --platform "${FACTORY_TEST_PLATFORM:?}" --cgroups=disabled --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" \
  -c '. /etc/os-release; printf "%s" "${VERSION_ID%%.*}"')
allowlists=(tests/profiles/base/rpm-verify.allow tests/profiles/bitbucket/rpm-verify.allow)
[[ ${base_major} == 10 ]] && allowlists+=(tests/profiles/base/rpm-verify.ubi10.allow)
scripts/assert_rpm_integrity.sh "${FACTORY_TEST_IMAGE}" \
  "${FACTORY_TEST_OUTPUT}/rpm-verify.txt" "${allowlists[@]}"

# The container shares the test step's own network namespace (inside a pod
# that is the pod's, never the node's): rootless port publishing needs
# pasta or slirp4netns, which need /dev/net/tun, which a pod does not have.
if [[ ${FACTORY_ENABLE_FULL_INTEGRATION:-true} == true ]]; then
  name="factory-bitbucket-${FACTORY_JOB_ID:-local}"
  podman run --detach --network host --platform "${FACTORY_TEST_PLATFORM:?}" --cgroups=disabled --name "${name}" \
    --env ELASTICSEARCH_ENABLED=false "${FACTORY_TEST_IMAGE}" >/dev/null
  trap 'podman logs "${name}" >"${FACTORY_TEST_OUTPUT}/container.log" 2>&1 || true; podman rm -f "${name}" >/dev/null 2>&1 || true' EXIT
  scripts/wait_http.sh "http://127.0.0.1:7990/status" 600
  podman stop --time 60 "${name}" >/dev/null
fi
