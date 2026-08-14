import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from factory.gate import gate_input


class GateInputTests(unittest.TestCase):
    def test_fcs_status_is_preserved_as_authoritative_policy_input(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            documents = {
                "sbom.json": {"bomFormat": "CycloneDX"},
                "findings.json": {"findings": [{"id": "legacy-critical", "severity": "Critical"}]},
                "compliance.json": {"passed": True},
                "tests.json": {"passed": True},
                "database.json": {"available": False},
                "fcs.json": {
                    "scanner": "crowdstrike-fcs",
                    "exitCode": 0,
                    "reportValid": True,
                    "sbomValid": True,
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
                root / "fcs.json",
            )

            self.assertTrue(result["fcs"]["assessmentPassed"])
            self.assertEqual(result["imageDigest"], "sha256:abc")
            self.assertEqual(result["fcs"]["scanner"], "crowdstrike-fcs")
            self.assertEqual(result["findings"][0]["id"], "legacy-critical")


if __name__ == "__main__":
    unittest.main()
