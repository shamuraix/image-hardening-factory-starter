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
bundle="${work_dir}/compliance-rootfs"
rootfs=$(scripts/unpack_image.sh "${work_dir}/image.oci.tar" "${bundle}")

cleanup() {
  rm -rf "${bundle}"
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
