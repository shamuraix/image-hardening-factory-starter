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
platform=$(yq -r '.build.platforms[0] // ""' "${catalog}")
if [[ -z "${platform}" || "${platform}" == "null" ]]; then
  platform=$(yq -r '.build.platforms[0] // ""' "${base_catalog}")
fi
arch=${platform##*/}
case "${arch}" in
  amd64) rpm_arch=x86_64 ;;
  arm64) rpm_arch=aarch64 ;;
  *) rpm_arch=${arch} ;;
esac

source_mode=${FACTORY_RPM_SOURCE_MODE:-$(yq -r '.build.rpm.source // ""' "${catalog}")}
[[ -n "${source_mode}" ]] || { echo "build.rpm.source must be set in ${catalog}" >&2; exit 2; }
# Behind a TLS-inspecting proxy build_image.sh mounts the runner's CA bundle
# into every RUN step at this path (factory.buildkit.CA_BUNDLE_TARGET); point
# dnf/microdnf at it instead of touching the image's trust store.
ca_option=""
if [[ -n ${FACTORY_CA_BUNDLE:-} ]]; then
  ca_option="sslcacert=/run/factory-ca-bundle.crt"
fi

case "${source_mode}" in
  private-mirror)
    # FACTORY_UBI_MIRROR_URL mirrors https://cdn-ubi.redhat.com/content/public/ubi/dist,
    # so one setting serves every UBI major and architecture.
    : "${FACTORY_UBI_MIRROR_URL:?FACTORY_UBI_MIRROR_URL is required for build.rpm.source=private-mirror}"
    : "${FACTORY_RPM_MIRROR_USERNAME:?FACTORY_RPM_MIRROR_USERNAME is required for build.rpm.source=private-mirror}"
    : "${FACTORY_RPM_MIRROR_PASSWORD:?FACTORY_RPM_MIRROR_PASSWORD is required for build.rpm.source=private-mirror}"
    mirror_root="${FACTORY_UBI_MIRROR_URL%/}/ubi${rpm_major}/${rpm_major}/${rpm_arch}"
    cat >"${output}" <<CFG
[factory-ubi-baseos]
name=Factory private UBI BaseOS mirror
baseurl=${mirror_root}/baseos/os/
enabled=1
gpgcheck=1
repo_gpgcheck=0
sslverify=1
${ca_option}
username=${FACTORY_RPM_MIRROR_USERNAME}
password=${FACTORY_RPM_MIRROR_PASSWORD}

[factory-ubi-appstream]
name=Factory private UBI AppStream mirror
baseurl=${mirror_root}/appstream/os/
enabled=1
gpgcheck=1
repo_gpgcheck=0
sslverify=1
${ca_option}
username=${FACTORY_RPM_MIRROR_USERNAME}
password=${FACTORY_RPM_MIRROR_PASSWORD}
CFG
    chmod 0600 "${output}"
    printf 'private-mirror:ubi%s:%s\n' "${rpm_major}" "${rpm_arch}"
    ;;
  public-upstream)
    # Red Hat's public UBI content, no subscription needed. Used by the kind
    # harness; production images pin build.rpm.source: private-mirror.
    upstream_root="https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi${rpm_major}/${rpm_major}/${rpm_arch}"
    cat >"${output}" <<CFG
[factory-ubi-upstream-baseos]
name=Factory public UBI BaseOS upstream
baseurl=${upstream_root%/}/baseos/os/
enabled=1
gpgcheck=1
repo_gpgcheck=0
sslverify=1
${ca_option}

[factory-ubi-upstream-appstream]
name=Factory public UBI AppStream upstream
baseurl=${upstream_root%/}/appstream/os/
enabled=1
gpgcheck=1
repo_gpgcheck=0
sslverify=1
${ca_option}
CFG
    chmod 0600 "${output}"
    printf 'public-upstream:ubi%s:%s\n' "${rpm_major}" "${rpm_arch}"
    ;;
  *)
    echo "unknown build.rpm.source: ${source_mode}" >&2
    exit 2
    ;;
esac
