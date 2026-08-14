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

if [[ -n ${FACTORY_UPSTREAM_UBI_VERSION:-} ]]; then
  case "${FACTORY_UPSTREAM_UBI_VERSION}" in
    9|10) ;;
    *) echo "FACTORY_UPSTREAM_UBI_VERSION must be 9 or 10" >&2; exit 2 ;;
  esac
  arch=${FACTORY_UPSTREAM_UBI_ARCH:-x86_64}
  prefix="https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi${FACTORY_UPSTREAM_UBI_VERSION}/${FACTORY_UPSTREAM_UBI_VERSION}/${arch}"
  cat >"${output}" <<EOF
[ubi-${FACTORY_UPSTREAM_UBI_VERSION}-baseos-rpms]
name=Red Hat UBI ${FACTORY_UPSTREAM_UBI_VERSION} BaseOS (local development)
baseurl=${prefix}/baseos/os/
enabled=1
gpgcheck=1
repo_gpgcheck=0
sslverify=1

[ubi-${FACTORY_UPSTREAM_UBI_VERSION}-appstream-rpms]
name=Red Hat UBI ${FACTORY_UPSTREAM_UBI_VERSION} AppStream (local development)
baseurl=${prefix}/appstream/os/
enabled=1
gpgcheck=1
repo_gpgcheck=0
sslverify=1
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
