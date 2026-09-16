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
                scans = work / "evidence/scans/grype"
                scans.mkdir(parents=True)
                (scans / "findings.json").write_text(findings)
                (scans / "database.json").write_text(json.dumps(database))
                (scans / "status.json").write_text('{"backend":"grype"}')
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
                        "FACTORY_SCANNER_BACKEND": "grype",
                        "FACTORY_IMAGE": "fixture",
                    },
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode == 73, reaches_gate, result.stderr)
                if not reaches_gate:
                    self.assertFalse((work / "evidence/findings.json").exists())
