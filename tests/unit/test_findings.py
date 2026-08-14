import tempfile
import unittest
from pathlib import Path

from factory.findings import normalize


class FindingTests(unittest.TestCase):
    def test_normalizes_grype_and_trivy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grype, trivy, kev = root / "grype.json", root / "trivy.json", root / "kev.json"
            grype.write_text(
                '{"matches":[{"vulnerability":{"id":"CVE-1","severity":"High","fix":{"versions":["2"]}},"artifact":{"name":"pkg","version":"1","purl":"pkg:rpm/pkg@1"}}]}',
                encoding="utf-8",
            )
            trivy.write_text(
                '{"Results":[{"Vulnerabilities":[{"VulnerabilityID":"CVE-2","PkgName":"jar","InstalledVersion":"1","FixedVersion":"","Severity":"CRITICAL"}]}]}',
                encoding="utf-8",
            )
            kev.write_text('{"vulnerabilities":[{"cveID":"CVE-2"}]}', encoding="utf-8")
            result = normalize(grype, trivy, kev=kev)
            self.assertEqual([finding["id"] for finding in result["findings"]], ["CVE-1", "CVE-2"])
            self.assertTrue(result["findings"][0]["fixAvailable"])
            self.assertFalse(result["findings"][1]["fixAvailable"])
            self.assertTrue(result["findings"][1]["knownExploited"])
