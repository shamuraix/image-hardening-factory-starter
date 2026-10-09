#!/usr/bin/env bash
set -euo pipefail
data=${FACTORY_SECURITY_DATA:-/opt/security-data}

case $# in
  1)
    catalog=${FACTORY_CATALOG_FILE:-}
    work_dir=${1:?work directory is required}
    ;;
  2)
    catalog=${1:?catalog file is required}
    [[ -f "${catalog}" ]] || { echo "catalog file does not exist: ${catalog}" >&2; exit 2; }
    work_dir=${2:?work directory is required}
    ;;
  *)
    echo "usage: scripts/scan_image.sh [catalog] <work-dir>" >&2
    exit 2
    ;;
esac
# Tekton sets FACTORY_IMAGE through scripts/tekton/env.sh; local runs
# (make local-assessment) derive it from the catalog entry.
if [[ -z ${FACTORY_IMAGE:-} ]]; then
  [[ -n ${catalog} ]] || { echo "FACTORY_IMAGE or a catalog file is required" >&2; exit 2; }
  FACTORY_IMAGE=$(yq -er '.metadata.name' "${catalog}")
fi
export FACTORY_IMAGE
evidence="${work_dir}/evidence"
scans="${evidence}/scans"
mkdir -p "${scans}"

export TRIVY_CACHE_DIR=${TRIVY_CACHE_DIR:-${data}/trivy}
export OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=${OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY:-${data}/osv}

kev=${FACTORY_KEV_PATH:-${data}/advisories/cisa-kev.json}
export FACTORY_KEV_PATH="${kev}"
# Grype runs first and is validated before anything else is trusted: an invalid
# database, a tampered or mismatched SBOM, or a stale KEV feed stops the scan
# here, so the assessment never sees partial evidence.
FACTORY_CATALOG_FILE="${catalog}" scripts/grype_scan_image.sh "${work_dir}"
cp "${scans}/grype/report.json" "${scans}/grype.json"
trivy image --skip-db-update --skip-java-db-update --input "${work_dir}/image.oci.tar" \
  --format json --output "${scans}/trivy.json"

if find "${work_dir}/context" -maxdepth 4 -type f \
  \( -name 'go.mod' -o -name 'pom.xml' -o -name 'package-lock.json' -o -name 'requirements.txt' \) \
  -print -quit | grep -q .; then
  osv-scanner scan source --recursive "${work_dir}/context" \
    --offline --format json >"${scans}/osv.json"
else
  printf '{"results":[]}\n' >"${scans}/osv.json"
fi

normalize_args=(
  --grype "${scans}/grype.json"
  --trivy "${scans}/trivy.json"
  --osv "${scans}/osv.json"
  --kev "${kev}"
  --output "${evidence}/findings.json"
)
# Use the baseline for Trivy and OSV findings too, but only when the Grype step
# verified its signature (it records the baseline digest when it did).
if jq -e '.baselineDigest != null' "${scans}/grype/status.json" >/dev/null; then
  normalize_args+=(--baseline "${FACTORY_BASELINE_DIR:-${data}/baselines}/${FACTORY_IMAGE}.json")
fi
python3 -m factory.cli normalize-findings "${normalize_args[@]}"
digest=$(skopeo inspect --format '{{.Digest}}' "oci-archive:${work_dir}/image.oci.tar")

malware_bundle="${work_dir}/malware-rootfs"
malware_rootfs=$(scripts/unpack_image.sh "${work_dir}/image.oci.tar" "${malware_bundle}")
cleanup_malware() {
  podman unshare rm -rf "${malware_bundle}" >/dev/null 2>&1 || true
}
trap cleanup_malware EXIT
podman unshare clamscan \
  --database="${data}/clamav" --recursive --infected "${malware_rootfs}" \
  >"${scans}/clamav.txt"

# Vulnerability data is as old as its oldest part: the gate's freshness check
# uses the earlier of the security-data bundle time and the Grype database
# build time.
generated_at=$(python3 - "$(cat "${data}/generated-at")" \
  "$(jq -er '.databaseBuilt' "${scans}/grype/status.json")" <<'PYTHON'
import sys
from datetime import datetime
times = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in sys.argv[1:]]
print(min(times).strftime("%Y-%m-%dT%H:%M:%SZ"))
PYTHON
)
jq -n \
  --arg generatedAt "${generated_at}" \
  --arg grype "$(grype version -o json | jq -r '.version')" \
  --arg trivy "$(trivy --version --format json | jq -r '.Version')" \
  --arg syft "$(syft version -o json | jq -r '.version')" \
  --arg osv "$(osv-scanner --version 2>&1 | head -n1)" \
  '{generatedAt:$generatedAt,scannerVersions:{grype:$grype,trivy:$trivy,syft:$syft,osvScanner:$osv}}' \
  >"${evidence}/database-status.json"
printf '%s\n' "$(syft version -o json | jq -r '.version')" >"${scans}/syft.version.txt"

warning_count=$(jq -r '.warnings // [] | length' "${evidence}/findings.json")
catalog_for_policy=${catalog:-}
block_critical=true
block_fixable_high=true
block_new_high=true
block_known_exploited=true
if [[ -n "${catalog_for_policy}" && -f "${catalog_for_policy}" ]]; then
  block_critical=$(yq -r '.policy.block.critical // true' "${catalog_for_policy}")
  block_fixable_high=$(yq -r '.policy.block.fixableHigh // true' "${catalog_for_policy}")
  block_new_high=$(yq -r '.policy.block.newHigh // true' "${catalog_for_policy}")
  block_known_exploited=$(yq -r '.policy.block.knownExploited // true' "${catalog_for_policy}")
fi
blocked_count=$(jq -r \
  --argjson blockCritical "${block_critical}" \
  --argjson blockFixableHigh "${block_fixable_high}" \
  --argjson blockNewHigh "${block_new_high}" \
  --argjson blockKnownExploited "${block_known_exploited}" \
  --arg image "${FACTORY_IMAGE:-}" \
  --slurpfile exceptions policies/exceptions/approved.json \
  '.findings
    | map(
        . as $f
        | (($exceptions[0][$image] // [])
          | any(.id == $f.id and .component == $f.component and .installedVersion == $f.installedVersion)
          ) as $isException
        | (($f.fixAvailable == true) and $isException) as $excluded
        | (($f.severity == "HIGH" or $f.severity == "CRITICAL") and $f.fixAvailable and (($f.inApplicationArchive // false) | not)) as $outsideWarnable
        | (
            $f.severity == "UNKNOWN"
            or ($blockCritical and $f.severity == "CRITICAL")
            or ($blockFixableHigh and $f.severity == "HIGH" and $f.fixAvailable)
            or ($blockNewHigh and $f.severity == "HIGH" and $f.new)
            or ($blockKnownExploited and $f.knownExploited)
          ) and (($f.inApplicationArchive // false) or ($outsideWarnable | not)) and ($excluded | not)
      )
    | map(select(.))
    | length' "${evidence}/findings.json")
assessment_passed=true
if ! jq -e --arg digest "${digest}" '.assessmentPassed == true and .digest == $digest' \
  "${scans}/grype/status.json" >/dev/null; then
  assessment_passed=false
fi
if [[ "${blocked_count}" -gt 0 ]]; then
  assessment_passed=false
fi
mkdir -p "${scans}/delegated"
jq -n \
  --arg assessedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg digest "${digest}" \
  --argjson warningCount "${warning_count}" \
  --argjson assessmentPassed "${assessment_passed}" \
  --slurpfile grype "${scans}/grype/status.json" \
  '{schemaVersion:"1.0",backend:"delegated-scanners",scanner:"trivy-grype-syft-osv-scanner",digest:$digest,assessmentPassed:$assessmentPassed,warningCount:$warningCount,assessedAt:$assessedAt,
    grypeValidation:($grype[0] | {validated:(.assessmentPassed == true and .digest == $digest),databaseBuilt,kevReleasedAt,kevDigest,baselineDigest,scannerVersion})}' \
  >"${scans}/delegated/status.json"
