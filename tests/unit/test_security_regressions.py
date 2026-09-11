import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory.gate import gate_input
from factory.schema import validate

ROOT = Path(__file__).resolve().parents[2]


class EvidenceRegressionTests(unittest.TestCase):
    def oscap_result(self, xml, status=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if xml is not None:
                (root / "arf.xml").write_text(xml)
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/parse_oscap.py"),
                    str(root / "arf.xml"),
                    str(root / "result.json"),
                    str(status),
                ],
                check=True,
            )
            return json.loads((root / "result.json").read_text())

    def test_missing_empty_and_inapplicable_oscap_reports_deny(self):
        for xml in (
            None,
            "<root/>",
            "<root><rule-result><result>notapplicable</result></rule-result></root>",
        ):
            with self.subTest(xml=xml):
                self.assertFalse(self.oscap_result(xml)["passed"])

    def test_oscap_requires_successful_exit_and_evaluated_pass(self):
        xml = '<root><rule-result idref="rule"><result>pass</result></rule-result></root>'
        self.assertTrue(self.oscap_result(xml)["passed"])
        self.assertFalse(self.oscap_result(xml, 2)["passed"])
        self.assertFalse(self.oscap_result(xml.replace("pass", "notchecked"))["passed"])

    def test_truthy_failure_strings_and_empty_sbom_do_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = [
                {"bomFormat": "CycloneDX"},
                [],
                {"passed": "false"},
                {"passed": "true"},
                {},
                {},
            ]
            paths = []
            for index, value in enumerate(values):
                path = root / f"{index}.json"
                path.write_text(json.dumps(value))
                paths.append(path)
            result = gate_input("test", "sha256:" + "a" * 64, *paths)
            self.assertFalse(result["sbomValid"])
            self.assertFalse(result["compliancePassed"])
            self.assertFalse(result["testsPassed"])
            self.assertEqual(result["findings"], [])

    def test_schema_enforces_oneof_siblings_and_maxitems(self):
        schema = {"type": "array", "maxItems": 1, "oneOf": [{"minItems": 1}]}
        self.assertTrue(validate([1, 2], schema))
        self.assertFalse(validate([1], schema))


if __name__ == "__main__":
    unittest.main()


class RemediationRegressionTests(unittest.TestCase):
    def test_new_protected_file_and_catalog_policy_changes_are_rejected(self):
        import yaml

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            path = root / "catalog/images/test.yaml"
            path.parent.mkdir(parents=True)
            data = {
                "source": {"revision": "a"},
                "product": {"version": "1"},
                "build": {"buildArgs": {}},
                "policy": {"critical": True},
            }
            path.write_text(yaml.safe_dump(data))
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-qm",
                    "baseline",
                ],
                cwd=root,
                check=True,
            )
            data["source"]["revision"] = "b"
            path.write_text(yaml.safe_dump(data))
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            command = [sys.executable, str(ROOT / "scripts/validate_remediation.py")]
            self.assertEqual(
                subprocess.run(command, cwd=root, capture_output=True, check=False).returncode, 0
            )
            data["policy"]["critical"] = False
            path.write_text(yaml.safe_dump(data))
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            self.assertNotEqual(
                subprocess.run(command, cwd=root, capture_output=True, check=False).returncode, 0
            )
            subprocess.run(["git", "reset", "--hard", "-q"], cwd=root, check=True)
            (root / "Jenkinsfile").write_text("malicious new pipeline")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            self.assertNotEqual(
                subprocess.run(command, cwd=root, capture_output=True, check=False).returncode, 0
            )
