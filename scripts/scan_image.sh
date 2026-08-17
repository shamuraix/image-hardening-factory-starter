#!/usr/bin/env bash
set -euo pipefail

work_dir=${1:?work directory is required}
evidence="${work_dir}/evidence"
scans="${evidence}/scans"
mkdir -p "${scans}"

: "${GRYPE_DB_CACHE_DIR:=/opt/security-data/grype}"
: "${TRIVY_CACHE_DIR:=/opt/security-data/trivy}"
: "${OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY:=/opt/security-data/osv}"

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
factory normalize-findings "${normalize_args[@]}"

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
  --arg osv "$(osv-scanner --version 2>&1 | head -n1)" \
  '{generatedAt:$generatedAt,scannerVersions:{grype:$grype,trivy:$trivy,osvScanner:$osv}}' \
  >"${evidence}/database-status.json"
