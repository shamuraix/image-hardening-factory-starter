#!/usr/bin/env bash
set -euo pipefail

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
evidence="${work_dir}/evidence"
scans="${evidence}/scans"
mkdir -p "${scans}"

export GRYPE_DB_CACHE_DIR=${GRYPE_DB_CACHE_DIR:-/opt/security-data/grype}
export TRIVY_CACHE_DIR=${TRIVY_CACHE_DIR:-/opt/security-data/trivy}
export OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=${OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY:-/opt/security-data/osv}

GRYPE_DB_AUTO_UPDATE=false grype "sbom:${evidence}/sbom.cdx.json" -o json >"${scans}/grype.json"
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
  --kev /opt/security-data/advisories/cisa-kev.json
  --output "${evidence}/findings.json"
)
baseline="/opt/security-data/baselines/${FACTORY_IMAGE}.json"
[[ -s "${baseline}" ]] && normalize_args+=(--baseline "${baseline}")
python3 -m factory.cli normalize-findings "${normalize_args[@]}"
digest=$(skopeo inspect --format '{{.Digest}}' "oci-archive:${work_dir}/image.oci.tar")

malware_bundle="${work_dir}/malware-rootfs"
malware_rootfs=$(scripts/unpack_image.sh "${work_dir}/image.oci.tar" "${malware_bundle}")
cleanup_malware() {
  podman unshare rm -rf "${malware_bundle}" >/dev/null 2>&1 || true
}
trap cleanup_malware EXIT
podman unshare clamscan \
  --database=/opt/security-data/clamav --recursive --infected "${malware_rootfs}" \
  >"${scans}/clamav.txt"

jq -n \
  --arg generatedAt "$(cat /opt/security-data/generated-at)" \
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
if [[ "${blocked_count}" -gt 0 ]]; then
  assessment_passed=false
fi
mkdir -p "${scans}/delegated"
jq -n \
  --arg assessedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg digest "${digest}" \
  --argjson warningCount "${warning_count}" \
  --argjson assessmentPassed "${assessment_passed}" \
  '{schemaVersion:"1.0",backend:"delegated-scanners",scanner:"trivy-grype-syft-osv-scanner",digest:$digest,assessmentPassed:$assessmentPassed,warningCount:$warningCount,assessedAt:$assessedAt}' \
  >"${scans}/delegated/status.json"
