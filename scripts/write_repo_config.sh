#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
output=${2:?output file is required}
base_kind=$(yq -r '.build.base.kind' "${catalog}")
if [[ "${base_kind}" == catalog ]]; then
  base=$(yq -r '.build.base.image' "${catalog}")
  case "${base}" in
    ubi9-minimal) repository=rpm-ubi9-snapshot-local ;;
    ubi10-minimal) repository=rpm-ubi10-snapshot-local ;;
    *) echo "unknown catalog RPM base: ${base}" >&2; exit 2 ;;
  esac
else
  version=$(yq -r '.product.version' "${catalog}")
  case "${version}" in
    9.*) repository=rpm-ubi9-snapshot-local ;;
    10.*) repository=rpm-ubi10-snapshot-local ;;
    *) echo "unable to determine RPM snapshot for ${catalog}" >&2; exit 2 ;;
  esac
fi
snapshot_id=$(scripts/rpm_snapshot_id.sh "${catalog}")

if [[ -n ${FACTORY_UBI_REPO_PREFIX:-} ]]; then
  : "${FACTORY_RPM_REPO_USERNAME:?FACTORY_RPM_REPO_USERNAME is required for the UBI cache}"
  : "${FACTORY_RPM_REPO_PASSWORD:?FACTORY_RPM_REPO_PASSWORD is required for the UBI cache}"
  cat >"${output}" <<EOF
[factory-ubi-baseos]
name=Factory internal UBI BaseOS cache
baseurl=${FACTORY_UBI_REPO_PREFIX%/}/baseos/os/
enabled=1
gpgcheck=1
repo_gpgcheck=0
sslverify=${FACTORY_RPM_SSLVERIFY:-1}
username=${FACTORY_RPM_REPO_USERNAME}
password=${FACTORY_RPM_REPO_PASSWORD}

[factory-ubi-appstream]
name=Factory internal UBI AppStream cache
baseurl=${FACTORY_UBI_REPO_PREFIX%/}/appstream/os/
enabled=1
gpgcheck=1
repo_gpgcheck=0
sslverify=${FACTORY_RPM_SSLVERIFY:-1}
username=${FACTORY_RPM_REPO_USERNAME}
password=${FACTORY_RPM_REPO_PASSWORD}
EOF
  chmod 0600 "${output}"
  printf '%s\n' "${snapshot_id}"
  exit 0
fi

if [[ -n ${FACTORY_RPM_BASE_URL:-} ]]; then
  cat >"${output}" <<EOF
[factory-snapshot]
name=Factory local immutable UBI snapshot
baseurl=${FACTORY_RPM_BASE_URL%/}/
enabled=1
gpgcheck=${FACTORY_RPM_GPGCHECK:-1}
repo_gpgcheck=${FACTORY_RPM_REPO_GPGCHECK:-1}
sslverify=${FACTORY_RPM_SSLVERIFY:-1}
EOF
  chmod 0600 "${output}"
  printf '%s\n' "${snapshot_id}"
  exit 0
fi

: "${ARTIFACTORY_URL:?}"
: "${ARTIFACTORY_READ_TOKEN:?}"

cat >"${output}" <<EOF
[factory-snapshot]
name=Factory immutable UBI snapshot
baseurl=${ARTIFACTORY_URL%/}/artifactory/${repository}/${snapshot_id}/
enabled=1
gpgcheck=1
repo_gpgcheck=1
sslverify=1
username=oidc
password=${ARTIFACTORY_READ_TOKEN}
EOF
chmod 0600 "${output}"
printf '%s\n' "${snapshot_id}"
