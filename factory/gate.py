from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def gate_input(
    image: str,
    image_digest: str,
    sbom_path: str | Path,
    findings_path: str | Path,
    compliance_path: str | Path,
    tests_path: str | Path,
    database_status_path: str | Path,
    fcs_status_path: str | Path,
) -> dict[str, Any]:
    sbom = json.loads(Path(sbom_path).read_text(encoding="utf-8"))
    findings = json.loads(Path(findings_path).read_text(encoding="utf-8"))
    compliance = json.loads(Path(compliance_path).read_text(encoding="utf-8"))
    tests = json.loads(Path(tests_path).read_text(encoding="utf-8"))
    database = json.loads(Path(database_status_path).read_text(encoding="utf-8"))
    fcs = json.loads(Path(fcs_status_path).read_text(encoding="utf-8"))
    return {
        "evaluatedAt": datetime.now(UTC).isoformat(),
        "image": image,
        "imageDigest": image_digest,
        "sbomValid": bool(sbom.get("bomFormat") or sbom.get("spdxVersion")),
        "findings": findings.get("findings", findings if isinstance(findings, list) else []),
        "compliancePassed": bool(compliance.get("passed", False)),
        "testsPassed": bool(tests.get("passed", False)),
        "database": database,
        "fcs": fcs,
    }


def write_gate_input(data: dict[str, Any], output: str | Path) -> None:
    Path(output).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
