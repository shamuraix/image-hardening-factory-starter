#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
output="${work_dir}/evidence/compliance"
mkdir -p "${output}"

profile=$(yq -r '.policy.profile' "${catalog}")
case "${profile}" in
  rhel9-container-stig|atlassian-rhel9-container)
    datastream=/opt/security-data/scap/ssg-rhel9-ds.xml
    xccdf_profile=xccdf_org.ssgproject.content_profile_stig
    ;;
  rhel10-container-stig-canary)
    datastream=/opt/security-data/scap/ssg-rhel10-ds.xml
    xccdf_profile=xccdf_org.ssgproject.content_profile_stig
    ;;
  *) echo "unknown compliance profile: ${profile}" >&2; exit 2 ;;
esac

[[ -s "${datastream}" ]]
name="factory-compliance-${CI_JOB_ID:-local}"
podman load -i "${work_dir}/image.oci.tar" >/dev/null
# shellcheck disable=SC1090,SC1091
source "${work_dir}/build.env"
: "${LOCAL_IMAGE_REF:?LOCAL_IMAGE_REF is missing from build.env}"
podman image exists "${LOCAL_IMAGE_REF}"
image=${LOCAL_IMAGE_REF}
container=$(podman create --name "${name}" "${image}")
rootfs=$(podman mount "${container}")

cleanup() {
  podman unmount "${container}" >/dev/null 2>&1 || true
  podman rm --force "${container}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

tailoring_args=()
if [[ -s "compliance/tailoring/${profile}.xml" ]]; then
  tailoring_args=(--tailoring-file "compliance/tailoring/${profile}.xml")
fi
set +e
oscap-chroot "${rootfs}" xccdf eval \
  --profile "${xccdf_profile}" \
  "${tailoring_args[@]}" \
  --results-arf "${output}/results-arf.xml" \
  --report "${output}/report.html" \
  "${datastream}"
oscap_status=$?
set -e

scripts/parse_oscap.py "${output}/results-arf.xml" "${output}/result.json" "${oscap_status}"
jq -e '.passed == true' "${output}/result.json" >/dev/null
