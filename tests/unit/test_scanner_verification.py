import base64
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


class ScannerVerificationTests(unittest.TestCase):
    def verify(self, backend, passed=True):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            digest = "sha256:" + "a" * 64

            def envelope(predicate):
                return json.dumps(
                    {
                        "payload": base64.b64encode(
                            json.dumps({"predicate": predicate}).encode()
                        ).decode()
                    }
                )

            (root / "gate").write_text(envelope({"allow": True, "scannerBackend": backend}))
            (root / "status").write_text(envelope({"assessmentPassed": passed, "digest": digest}))
            mock = root / "cosign"
            mock.write_text("""#!/usr/bin/env bash
set -eu
if [[ "$*" == *":gate:v1"* ]]; then
  cat "$FIXTURE/gate"
elif [[ "$*" == *"-decision:v1"* ]]; then
  [[ "$*" == *":delegated-scanners-decision:v1"* ]] || exit 1
  cat "$FIXTURE/status"
fi
""")
            mock.chmod(0o755)
            return subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts/verify_release_evidence.sh"),
                    "registry/test@" + digest,
                ],
                env={
                    **os.environ,
                    "PATH": str(root) + ":" + os.environ["PATH"],
                    "FIXTURE": str(root),
                    "COSIGN_PUBLIC_KEY": "test",
                    "FACTORY_RELEASE_ENV": "commercial",
                },
                capture_output=True,
                check=False,
            ).returncode

    def test_delegated_scanner_backend_requires_verified_assessment(self):
        self.assertEqual(self.verify("delegated-scanners"), 0)
        self.assertNotEqual(self.verify("delegated-scanners", passed=False), 0)
        self.assertNotEqual(self.verify("unknown"), 0)


if __name__ == "__main__":
    unittest.main()
