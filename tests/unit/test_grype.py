import hashlib
import json
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from factory.grype import approved_baseline, assess, validate_kev, validate_report


class GrypeAssessmentTests(unittest.TestCase):
    def test_invalid_reports_and_databases_fail_closed(self):
        report = {"descriptor": {"name": "grype"}, "matches": []}
        database = {"valid": True, "built": "2026-09-15T00:00:00Z"}
        validate_report(report, database)
        cases = [
            ({}, database),
            ({**report, "matches": None}, database),
            (report, {**database, "valid": False}),
            (report, {**database, "built": "invalid"}),
            (report, {**database, "built": "2026-09-15T00:00:00"}),
            (
                {
                    **report,
                    "matches": [
                        {"vulnerability": {"id": "CVE-x"}, "artifact": {"name": "openssl"}}
                    ],
                },
                database,
            ),
        ]
        for candidate, db in cases:
            with (
                self.subTest(candidate=candidate, db=db),
                self.assertRaises((ValueError, TypeError, KeyError)),
            ):
                validate_report(candidate, db)

    def test_assessment_binds_sbom_and_enriches_findings(self):
        with TemporaryDirectory() as directory:
            work = Path(directory)
            evidence = work / "evidence"
            scans = evidence / "scans/grype"
            scans.mkdir(parents=True)
            digest = "sha256:" + "a" * 64
            identity = {"digest": digest}
            for name in ("sbom.cdx.json", "sbom.spdx.json"):
                (evidence / name).write_text("{}")
                identity[name] = hashlib.sha256(b"{}").hexdigest()
            (evidence / "sbom.identity.json").write_text(json.dumps(identity))
            (work / "image-metadata.json").write_text(json.dumps({"digest": digest}))
            (scans / "database.json").write_text(
                json.dumps({"valid": True, "built": "2026-09-15T00:00:00Z"})
            )
            report = {
                "descriptor": {"name": "grype", "version": "0.95.0"},
                "matches": [
                    {
                        "vulnerability": {
                            "id": "CVE-test",
                            "severity": "Critical",
                            "fix": {"versions": ["2"]},
                        },
                        "artifact": {"name": "openssl", "version": "1"},
                    }
                ],
            }
            (scans / "report.json").write_text(json.dumps(report))
            kev = work / "kev.json"
            kev.write_text(
                json.dumps(
                    {
                        "dateReleased": datetime.now(UTC).isoformat(),
                        "vulnerabilities": [{"cveID": "CVE-test"}],
                    }
                )
            )
            assess(work, digest, kev)
            finding = json.loads((scans / "findings.json").read_text())["findings"][0]
            self.assertTrue(finding["knownExploited"])
            self.assertTrue(finding["fixAvailable"])
            self.assertEqual(finding["severity"], "CRITICAL")
            (evidence / "sbom.cdx.json").write_text('{"tampered":true}')
            with self.assertRaisesRegex(ValueError, "changed"):
                assess(work, digest, kev)
            with self.assertRaisesRegex(ValueError, "another image"):
                assess(work, "sha256:" + "b" * 64, kev)

    def test_kev_freshness_and_missing_timestamp(self):
        for offset in (-73, 1):
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "stale"):
                validate_kev(
                    {
                        "vulnerabilities": [],
                        "dateReleased": (datetime.now(UTC) + timedelta(hours=offset)).isoformat(),
                    },
                    72,
                )
        with self.assertRaises(KeyError):
            validate_kev({"vulnerabilities": []}, 72)

    def test_baseline_requires_signature_and_matching_approval(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            baseline = {
                "approved": True,
                "image": "fixture",
                "imageDigest": "sha256:" + "a" * 64,
                "findings": [{"correlationKey": "CVE-test|openssl|1"}],
            }
            path.write_text(json.dumps(baseline))
            with patch("factory.grype.subprocess.run") as verify:
                self.assertEqual(
                    approved_baseline(path, Path("key"), "fixture"), {"CVE-test|openssl|1"}
                )
                self.assertIn(str(path) + ".sig", verify.call_args.args[0])
                with self.assertRaisesRegex(ValueError, "identity"):
                    approved_baseline(path, Path("key"), "different-image")
            with (
                patch(
                    "factory.grype.subprocess.run",
                    side_effect=subprocess.CalledProcessError(1, "cosign"),
                ),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                approved_baseline(path, Path("key"), "fixture")
