#!/usr/bin/env bash
set -euo pipefail

archive=${1:?OCI archive is required}
output=${2:?output directory is required}
mkdir -p "${output}"

syft "oci-archive:${archive}" --scope all-layers -o "cyclonedx-json=${output}/sbom.cdx.json"
syft "oci-archive:${archive}" --scope all-layers -o "spdx-json=${output}/sbom.spdx.json"
jq -e '.bomFormat == "CycloneDX" and (.components | type == "array")' "${output}/sbom.cdx.json" >/dev/null
jq -e '.spdxVersion | startswith("SPDX-")' "${output}/sbom.spdx.json" >/dev/null
python3 - "${archive}" "${output}" <<'PYTHON'
import hashlib
import json
import subprocess
import sys
from pathlib import Path
archive, output = sys.argv[1], Path(sys.argv[2])
identity = {"digest": subprocess.check_output(
    ["skopeo", "inspect", "--format", "{{.Digest}}", "oci-archive:" + archive], text=True).strip()}
for name in ("sbom.cdx.json", "sbom.spdx.json"):
    identity[name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
(output / "sbom.identity.json").write_text(json.dumps(identity) + "\n")
PYTHON
