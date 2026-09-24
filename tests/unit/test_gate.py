import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from factory.gate import gate_input


class GateInputTests(unittest.TestCase):
    def test_assessment_status_is_preserved_as_authoritative_policy_input(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            documents = {
                "sbom.json": {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []},
                "findings.json": {
                    "findings": [{"id": "legacy-critical", "severity": "Critical"}],
                    "warnings": ["example"],
                },
                "compliance.json": {"passed": True},
                "tests.json": {"passed": True},
                "database.json": {"generatedAt": "2026-09-15T00:00:00Z"},
                "assessment.json": {
                    "backend": "delegated-scanners",
                    "scanner": "trivy-grype-syft-osv-scanner",
                    "digest": "sha256:abc",
                    "assessmentPassed": True,
                },
            }
            for name, document in documents.items():
                (root / name).write_text(json.dumps(document), encoding="utf-8")

            result = gate_input(
                "candidate",
                "sha256:abc",
                root / "sbom.json",
                root / "findings.json",
                root / "compliance.json",
                root / "tests.json",
                root / "database.json",
                root / "assessment.json",
            )

            self.assertTrue(result["assessment"]["assessmentPassed"])
            self.assertEqual(result["imageDigest"], "sha256:abc")
            self.assertEqual(result["assessment"]["scanner"], "trivy-grype-syft-osv-scanner")
            self.assertEqual(result["findings"][0]["id"], "legacy-critical")
            self.assertEqual(result["warnings"], ["example"])


if __name__ == "__main__":
    unittest.main()
