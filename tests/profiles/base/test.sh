#!/usr/bin/env bash
set -euo pipefail

: "${FACTORY_TEST_IMAGE:?}"
: "${FACTORY_TEST_OUTPUT:?}"
podman run --rm --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" -c '
  set -e
  grep -Eq "^(ID|ID_LIKE)=.*(rhel|fedora)" /etc/os-release
  test -f /etc/crypto-policies/config
  test "$(stat -c %a /tmp)" = 1777
  test "$(stat -c %a /var/tmp)" = 1777
  update-ca-trust check
'
base_major=$(podman run --rm --entrypoint /bin/bash "${FACTORY_TEST_IMAGE}" \
  -c '. /etc/os-release; printf "%s" "${VERSION_ID%%.*}"')
allowlists=(tests/profiles/base/rpm-verify.allow)
[[ ${base_major} == 10 ]] && allowlists+=(tests/profiles/base/rpm-verify.ubi10.allow)
scripts/assert_rpm_integrity.sh "${FACTORY_TEST_IMAGE}" \
  "${FACTORY_TEST_OUTPUT}/rpm-verify.txt" "${allowlists[@]}"
