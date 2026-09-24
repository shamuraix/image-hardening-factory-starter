#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
output=${2:?output file is required}

base_kind=$(yq -r '.build.base.kind' "${catalog}")
if [[ "${base_kind}" == catalog ]]; then
  base_catalog="$(dirname "${catalog}")/$(yq -r '.build.base.image' "${catalog}").yaml"
else
  base_catalog="${catalog}"
fi
rpm_major=$(yq -r '.product.version | split(".")[0]' "${base_catalog}")
arch=$(yq -r '.build.platforms[0] | split("/")[1]' "${catalog}")
[[ "${arch}" == amd64 ]] && rpm_arch=x86_64 || rpm_arch=${arch}

source_mode=${FACTORY_RPM_SOURCE_MODE:-$(yq -r '.build.rpm.source // "private-mirror"' "${catalog}")}

if [[ -n ${FACTORY_RPM_BASE_URL:-} ]]; then
  cat >"${output}" <<CFG
[factory-local-rpm]
name=Factory local RPM source
baseurl=${FACTORY_RPM_BASE_URL%/}/
enabled=1
gpgcheck=${FACTORY_RPM_GPGCHECK:-1}
repo_gpgcheck=${FACTORY_RPM_REPO_GPGCHECK:-1}
sslverify=${FACTORY_RPM_SSLVERIFY:-1}
CFG
  chmod 0600 "${output}"
  printf 'local-source:ubi%s:%s\n' "${rpm_major}" "${rpm_arch}"
  exit 0
fi
case "${source_mode}" in
  private-mirror)
    : "${FACTORY_UBI_REPO_PREFIX:?FACTORY_UBI_REPO_PREFIX is required for build.rpm.source=private-mirror}"
    : "${FACTORY_RPM_REPO_USERNAME:?FACTORY_RPM_REPO_USERNAME is required for build.rpm.source=private-mirror}"
    : "${FACTORY_RPM_REPO_PASSWORD:?FACTORY_RPM_REPO_PASSWORD is required for build.rpm.source=private-mirror}"
    cat >"${output}" <<CFG
[factory-ubi-baseos]
name=Factory private UBI BaseOS mirror
baseurl=${FACTORY_UBI_REPO_PREFIX%/}/baseos/os/
enabled=1
gpgcheck=${FACTORY_RPM_GPGCHECK:-1}
repo_gpgcheck=0
sslverify=${FACTORY_RPM_SSLVERIFY:-1}
username=${FACTORY_RPM_REPO_USERNAME}
******

[factory-ubi-appstream]
name=Factory private UBI AppStream mirror
baseurl=${FACTORY_UBI_REPO_PREFIX%/}/appstream/os/
enabled=1
gpgcheck=${FACTORY_RPM_GPGCHECK:-1}
repo_gpgcheck=0
sslverify=${FACTORY_RPM_SSLVERIFY:-1}
username=${FACTORY_RPM_REPO_USERNAME}
******
CFG
    chmod 0600 "${output}"
    printf 'private-mirror:ubi%s:%s\n' "${rpm_major}" "${rpm_arch}"
    ;;
  public-upstream)
    upstream_root=${FACTORY_RPM_UPSTREAM_UBI_BASE:-}
    if [[ -z "${upstream_root}" ]]; then
      upstream_root="https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi${rpm_major}/${rpm_major}/${rpm_arch}"
    fi
    cat >"${output}" <<CFG
[factory-ubi-upstream-baseos]
name=Factory public UBI BaseOS upstream
baseurl=${upstream_root%/}/baseos/os/
enabled=1
gpgcheck=${FACTORY_RPM_GPGCHECK:-1}
repo_gpgcheck=0
sslverify=${FACTORY_RPM_SSLVERIFY:-1}

[factory-ubi-upstream-appstream]
name=Factory public UBI AppStream upstream
baseurl=${upstream_root%/}/appstream/os/
enabled=1
gpgcheck=${FACTORY_RPM_GPGCHECK:-1}
repo_gpgcheck=0
sslverify=${FACTORY_RPM_SSLVERIFY:-1}
CFG
    chmod 0600 "${output}"
    printf 'public-upstream:ubi%s:%s\n' "${rpm_major}" "${rpm_arch}"
    ;;
  *)
    echo "unknown build.rpm.source: ${source_mode}" >&2
    exit 2
    ;;
esac
