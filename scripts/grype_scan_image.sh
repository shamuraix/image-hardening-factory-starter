#!/usr/bin/env bash
set -euo pipefail
work_dir=${1:?work directory is required}
scans="${work_dir}/evidence/scans/grype"
mkdir -p "${scans}"
# Remove success from any previous attempt before invoking external tools.
printf '{"backend":"grype","assessmentPassed":false}\n' >"${scans}/status.json"
export GRYPE_DB_CACHE_DIR=${GRYPE_DB_CACHE_DIR:-/opt/security-data/grype}
export GRYPE_DB_AUTO_UPDATE=false
export GRYPE_CHECK_FOR_APP_UPDATE=false
# No --fail-on: vulnerability thresholds are evaluated by OPA. Operational
# errors remain fatal and cannot switch the selected backend.
grype db status -o json >"${scans}/database.json"
grype "sbom:${work_dir}/evidence/sbom.cdx.json" -o json >"${scans}/report.json"
digest=$(skopeo inspect --format '{{.Digest}}' "oci-archive:${work_dir}/image.oci.tar")
maximum_age=72
if [[ -n ${FACTORY_CATALOG_FILE:-} ]]; then
  maximum_age=$(yq -er '.policy.maximumDatabaseAgeHours' "${FACTORY_CATALOG_FILE}")
fi
python3 -m factory.grype "${work_dir}" "${digest}" \
  "${FACTORY_KEV_PATH:-/opt/security-data/advisories/cisa-kev.json}" "${maximum_age}"
