#!/usr/bin/env bash
set -euo pipefail

: "${FACTORY_TEST_IMAGE:?}"
: "${FACTORY_TEST_OUTPUT:?}"
inspect=$(podman image inspect "${FACTORY_TEST_IMAGE}")
[[ $(jq -r '.[0].Config.User' <<<"${inspect}") == jira ]]
jq -e '.[0].Config.ExposedPorts | has("8080/tcp") and has("40001/tcp")' <<<"${inspect}" >/dev/null
podman run --rm --network none --platform "${FACTORY_TEST_PLATFORM:?}" --cgroups=disabled --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" -c '
  set -e
  java -version
  test -x /usr/bin/tini
  test -x /entrypoint.py
  test "$(id -u)" = 2001
  find /var/atlassian/application-data/jira/plugins/installed-plugins -name "*.jar" -print -quit | grep -q .
  ! compgen -G "/opt/jira-servicedesk-application-*.obr" >/dev/null
'
base_major=$(podman run --rm --network none --platform "${FACTORY_TEST_PLATFORM:?}" --cgroups=disabled --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" \
  -c '. /etc/os-release; printf "%s" "${VERSION_ID%%.*}"')
allowlists=(tests/profiles/base/rpm-verify.allow tests/profiles/jira/rpm-verify.allow)
[[ ${base_major} == 10 ]] && allowlists+=(tests/profiles/base/rpm-verify.ubi10.allow)
scripts/assert_rpm_integrity.sh "${FACTORY_TEST_IMAGE}" \
  "${FACTORY_TEST_OUTPUT}/rpm-verify.txt" "${allowlists[@]}"

# The container shares the test step's own network namespace (inside a pod
# that is the pod's, never the node's): rootless port publishing needs
# pasta or slirp4netns, which need /dev/net/tun, which a pod does not have.
if [[ ${FACTORY_ENABLE_FULL_INTEGRATION:-true} == true ]]; then
  name="factory-jira-${FACTORY_JOB_ID:-local}"
  podman run --detach --network host --platform "${FACTORY_TEST_PLATFORM:?}" --cgroups=disabled --name "${name}" \
    "${FACTORY_TEST_IMAGE}" >/dev/null
  trap 'podman logs "${name}" >"${FACTORY_TEST_OUTPUT}/container.log" 2>&1 || true; podman rm -f "${name}" >/dev/null 2>&1 || true' EXIT
  scripts/wait_http.sh "http://127.0.0.1:8080/status" 600
  podman stop --time 60 "${name}" >/dev/null
fi
