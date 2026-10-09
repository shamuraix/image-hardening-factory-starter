#!/usr/bin/env bash
# Build the signed offline security-data bundle every scan and compliance stage
# uses. Runs in the connected security-data pipeline (intake trust class).
#
# Contents: Grype database, Trivy vulnerability and Java databases, OSV offline
# databases (Go, Maven, npm, PyPI), ClamAV signatures, the CISA KEV catalog,
# ComplianceAsCode SCAP datastreams, a generated-at timestamp, and a manifest of
# SHA-256 digests. Output: <output>.tar.gz and <output>.tar.gz.sig.
set -euo pipefail

output=${1:?output directory is required}
: "${COSIGN_INTAKE_KEY_REF:?}"
: "${CISA_KEV_URL:?CISA_KEV_URL is required}"
: "${COMPLIANCE_AS_CODE_DATASTREAM_DIR:?COMPLIANCE_AS_CODE_DATASTREAM_DIR is required}"
rm -rf "${output}"
mkdir -p "${output}"/{grype,trivy,osv,clamav,advisories,scap}

GRYPE_DB_CACHE_DIR="${output}/grype" grype db update
TRIVY_CACHE_DIR="${output}/trivy" trivy image --download-db-only
TRIVY_CACHE_DIR="${output}/trivy" trivy image --download-java-db-only

# OSV downloads databases only for ecosystems it finds while scanning, so scan a
# seed that names one package for each ecosystem scan_image.sh looks for
# (go.mod, pom.xml, package-lock.json, requirements.txt). Custom-format input:
# https://github.com/google/osv-scanner/blob/v2.2.3/docs/supported_languages_and_lockfiles.md
seed=$(mktemp -d)
trap 'rm -rf "${seed}"' EXIT
cat >"${seed}/osv-scanner.json" <<'JSON'
{"results": [{"packages": [
  {"package": {"name": "golang.org/x/text", "version": "0.3.0", "ecosystem": "Go"}},
  {"package": {"name": "org.apache.commons:commons-text", "version": "1.9", "ecosystem": "Maven"}},
  {"package": {"name": "lodash", "version": "4.17.20", "ecosystem": "npm"}},
  {"package": {"name": "requests", "version": "2.19.0", "ecosystem": "PyPI"}}
]}]}
JSON
# Exit status 1 only means the seed packages have known vulnerabilities.
OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY="${output}/osv" \
  osv-scanner --offline-vulnerabilities --download-offline-databases \
  --lockfile "osv-scanner:${seed}/osv-scanner.json" >/dev/null || [[ $? -eq 1 ]]
find "${output}/osv" -type f -print -quit | grep -q . || {
  echo "OSV offline databases were not downloaded" >&2
  exit 1
}

freshclam --datadir="${output}/clamav"

# Must resolve through the connected allowlist or a reviewed internal proxy.
curl --fail --silent --show-error --location \
  --output "${output}/advisories/cisa-kev.json" "${CISA_KEV_URL}"
jq -e '(.dateReleased | type == "string") and (.vulnerabilities | type == "array")' \
  "${output}/advisories/cisa-kev.json" >/dev/null

cp -a "${COMPLIANCE_AS_CODE_DATASTREAM_DIR}/." "${output}/scap/"
for datastream in ssg-rhel9-ds.xml ssg-rhel10-ds.xml; do
  [[ -s ${output}/scap/${datastream} ]] || { echo "missing SCAP datastream ${datastream}" >&2; exit 1; }
done
date -u +%Y-%m-%dT%H:%M:%SZ >"${output}/generated-at"

# Relative paths, so the manifest verifies wherever the bundle is unpacked.
# shellcheck disable=SC2094  # find excludes the manifest it is writing.
(
  cd "${output}"
  find . -type f ! -name manifest.sha256 -print0 | sort -z | xargs -0 sha256sum >manifest.sha256
)
tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
  -C "${output}" -czf "${output}.tar.gz" .
cosign sign-blob --yes --tlog-upload=false --key "${COSIGN_INTAKE_KEY_REF}" \
  --output-signature "${output}.tar.gz.sig" "${output}.tar.gz"
