#!/usr/bin/env bash
# Run Grype against the candidate's SBOM and validate the result before any of
# it is trusted (factory/grype.py). Called by scripts/scan_image.sh.
#
# Fails the scan, and therefore the gate, when:
#   - the offline Grype database is invalid or its build time has no timezone;
#   - the Grype report is malformed;
#   - the SBOM was generated for a different digest or changed afterwards;
#   - the CISA KEV feed is older than policy.maximumDatabaseAgeHours or future-dated;
#   - a baseline is present with a signature that does not verify.
#
# Outputs under <work>/evidence/scans/grype/: database.json, report.json,
# findings.json, status.json.
set -euo pipefail
data=${FACTORY_SECURITY_DATA:-/opt/security-data}
work_dir=${1:?work directory is required}
scans="${work_dir}/evidence/scans/grype"
mkdir -p "${scans}"
# Remove success from any previous attempt before invoking external tools.
printf '{"backend":"grype","assessmentPassed":false}\n' >"${scans}/status.json"
export GRYPE_DB_CACHE_DIR=${GRYPE_DB_CACHE_DIR:-${data}/grype}
export GRYPE_DB_AUTO_UPDATE=false
export GRYPE_CHECK_FOR_APP_UPDATE=false
# No --fail-on: vulnerability thresholds are evaluated by OPA. Operational
# errors remain fatal.
grype db status -o json >"${scans}/database.json"
grype "sbom:${work_dir}/evidence/sbom.cdx.json" -o json >"${scans}/report.json"
digest=$(skopeo inspect --format '{{.Digest}}' "oci-archive:${work_dir}/image.oci.tar")
maximum_age=72
if [[ -n ${FACTORY_CATALOG_FILE:-} ]]; then
  maximum_age=$(yq -er '.policy.maximumDatabaseAgeHours' "${FACTORY_CATALOG_FILE}")
fi

# A baseline marks findings that were already accepted as "not new", which
# relaxes the gate's newHigh rule, so only a signed baseline is used. Without a
# signature and key, every finding counts as new (the stricter outcome).
baseline_dir=${FACTORY_BASELINE_DIR:-${data}/baselines}
baseline="${baseline_dir}/${FACTORY_IMAGE:?FACTORY_IMAGE is required}.json"
public_key=${FACTORY_BASELINE_PUBLIC_KEY:-${baseline_dir}/cosign.pub}
if [[ -s ${baseline} && -s ${baseline}.sig && -s ${public_key} ]]; then
  export FACTORY_GRYPE_BASELINE="${baseline}" FACTORY_BASELINE_PUBLIC_KEY="${public_key}"
elif [[ -s ${baseline} ]]; then
  echo "ignoring unsigned baseline ${baseline}: every finding is treated as new" >&2
fi

python3 -m factory.grype "${work_dir}" "${digest}" \
  "${FACTORY_KEV_PATH:-${data}/advisories/cisa-kev.json}" "${maximum_age}"
