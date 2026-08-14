#!/usr/bin/env bash
set -euo pipefail

archive=${1:?OCI archive is required}
output=${2:?output directory is required}
mkdir -p "${output}"

syft "oci-archive:${archive}" --scope all-layers -o "cyclonedx-json=${output}/sbom.cdx.json"
syft "oci-archive:${archive}" --scope all-layers -o "spdx-json=${output}/sbom.spdx.json"
jq -e '.bomFormat == "CycloneDX" and (.components | type == "array")' "${output}/sbom.cdx.json" >/dev/null
jq -e '.spdxVersion | startswith("SPDX-")' "${output}/sbom.spdx.json" >/dev/null
