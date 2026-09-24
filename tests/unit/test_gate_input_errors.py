import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


class GateInputErrorTests(unittest.TestCase):
    def test_malformed_authoritative_evidence_never_becomes_empty_findings(self):
        cases = [
            ('{"findings":[]}', {"valid": True}, True),
            ("{}", {"valid": True}, False),
            ('{"findings":{}}', {"valid": True}, False),
            ("not json", {"valid": True}, False),
            ('{"findings":[]}', {"valid": False}, False),
        ]
        for findings, database, reaches_gate in cases:
            with self.subTest(findings=findings, database=database), TemporaryDirectory() as d:
                work = Path(d)
                evidence = work / "evidence"
                scans = evidence / "scans/delegated"
                scans.mkdir(parents=True)
                (evidence / "findings.json").write_text(findings)
                db = (
                    {"generatedAt": "2026-09-15T00:00:00Z", "scannerVersions": {}}
                    if database.get("valid")
                    else {}
                )
                (evidence / "database-status.json").write_text(json.dumps(db))
                (scans / "status.json").write_text(
                    '{"backend":"delegated-scanners","assessmentPassed":true,"scanner":"trivy-grype-syft-osv-scanner","digest":"sha256:'
                    + "a" * 64
                    + '"}'
                )
                (evidence / "sbom.cdx.json").write_text(
                    json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []})
                )
                (evidence / "compliance/result.json").parent.mkdir(parents=True, exist_ok=True)
                (evidence / "compliance/result.json").write_text(json.dumps({"passed": True}))
                (evidence / "tests/result.json").parent.mkdir(parents=True, exist_ok=True)
                (evidence / "tests/result.json").write_text(json.dumps({"passed": True}))
                (work / "image-metadata.json").write_text(
                    json.dumps({"digest": "sha256:" + "a" * 64})
                )
                tool = work / "python3"
                tool.write_text("#!/bin/sh\nexit 73\n")
                tool.chmod(0o755)
                result = subprocess.run(
                    ["bash", "scripts/evaluate_gate.sh", "unused", str(work)],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "PATH": str(work) + ":" + os.environ["PATH"],
                        "FACTORY_SCANNER_BACKEND": "delegated-scanners",
                        "FACTORY_IMAGE": "fixture",
                    },
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode == 73, reaches_gate, result.stderr)
