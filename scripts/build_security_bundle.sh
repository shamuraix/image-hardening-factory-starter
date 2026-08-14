#!/usr/bin/env bash
set -euo pipefail

output=${1:?output directory is required}
: "${COSIGN_INTAKE_KEY_REF:?}"
mkdir -p "${output}"/{grype,trivy,osv,clamav,advisories,scap}

GRYPE_DB_CACHE_DIR="${output}/grype" grype db update
TRIVY_CACHE_DIR="${output}/trivy" trivy image --download-db-only
TRIVY_CACHE_DIR="${output}/trivy" trivy image --download-java-db-only
OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY="${output}/osv" \
  osv-scanner --download-offline-databases
freshclam --datadir="${output}/clamav"

# These URLs must resolve through the connected intake allowlist. Override them
# with reviewed internal proxies where required.
curl --fail --silent --show-error --location \
  --output "${output}/advisories/cisa-kev.json" \
  "${CISA_KEV_URL:?CISA_KEV_URL is required}"
curl --fail --silent --show-error --location \
  --output "${output}/advisories/epss.csv.gz" \
  "${EPSS_URL:?EPSS_URL is required}"

cp -a "${COMPLIANCE_AS_CODE_DATASTREAM_DIR:?}/." "${output}/scap/"
date -u +%Y-%m-%dT%H:%M:%SZ >"${output}/generated-at"

find "${output}" -type f ! -name manifest.sha256 -print0 \
  | sort -z \
  | xargs -0 sha256sum >"${output}/manifest.sha256"
tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
  -C "${output}" -czf "${output}.tar.gz" .
cosign sign-blob --yes --tlog-upload=false --key "${COSIGN_INTAKE_KEY_REF}" \
  --output-signature "${output}.tar.gz.sig" "${output}.tar.gz"
