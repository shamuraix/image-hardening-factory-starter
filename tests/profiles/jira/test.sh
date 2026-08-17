#!/usr/bin/env bash
set -euo pipefail

: "${FACTORY_TEST_IMAGE:?}"
: "${FACTORY_TEST_OUTPUT:?}"
inspect=$(podman image inspect "${FACTORY_TEST_IMAGE}")
[[ $(jq -r '.[0].Config.User' <<<"${inspect}") == jira ]]
jq -e '.[0].Config.ExposedPorts | has("8080/tcp") and has("40001/tcp")' <<<"${inspect}" >/dev/null
podman run --rm --cgroups=disabled --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" -c '
  set -e
  java -version
  test -x /usr/bin/tini
  test -x /entrypoint.py
  test "$(id -u)" = 2001
  find /var/atlassian/application-data/jira/plugins/installed-plugins -name "*.jar" -print -quit | grep -q .
  ! test -e /opt/jira-servicedesk-application-${JSM_VERSION:-11.3.10}.obr
'
base_major=$(podman run --rm --cgroups=disabled --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" \
  -c '. /etc/os-release; printf "%s" "${VERSION_ID%%.*}"')
allowlists=(tests/profiles/base/rpm-verify.allow tests/profiles/jira/rpm-verify.allow)
[[ ${base_major} == 10 ]] && allowlists+=(tests/profiles/base/rpm-verify.ubi10.allow)
scripts/assert_rpm_integrity.sh "${FACTORY_TEST_IMAGE}" \
  "${FACTORY_TEST_OUTPUT}/rpm-verify.txt" "${allowlists[@]}"

if [[ ${FACTORY_ENABLE_FULL_INTEGRATION:-true} == true ]]; then
  name="factory-jira-${CI_JOB_ID:-local}"
  podman run --detach --cgroups=disabled --name "${name}" \
    --publish 127.0.0.1::8080 "${FACTORY_TEST_IMAGE}" >/dev/null
  trap 'podman logs "${name}" >"${FACTORY_TEST_OUTPUT}/container.log" 2>&1 || true; podman rm -f "${name}" >/dev/null 2>&1 || true' EXIT
  port=$(podman port "${name}" 8080/tcp | awk -F: 'NR==1{print $NF}')
  scripts/wait_http.sh "http://127.0.0.1:${port}/status" 600
  podman stop --time 60 "${name}" >/dev/null
fi
