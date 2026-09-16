import base64
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


class ScannerVerificationTests(unittest.TestCase):
    def verify(self, backend, status_backend, passed=True):
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
  [[ "$*" == *":${STATUS_BACKEND}-decision:v1"* ]] || exit 1
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
                    "STATUS_BACKEND": status_backend,
                    "COSIGN_PUBLIC_KEY": "test",
                    "FACTORY_RELEASE_ENV": "commercial",
                },
                capture_output=True,
                check=False,
            ).returncode

    def test_each_backend_requires_its_own_verified_assessment(self):
        for backend in ("fcs", "grype"):
            with self.subTest(backend=backend):
                self.assertEqual(self.verify(backend, backend), 0)
                self.assertNotEqual(self.verify(backend, backend, passed=False), 0)
                other = "grype" if backend == "fcs" else "fcs"
                self.assertNotEqual(self.verify(backend, other), 0)
        self.assertNotEqual(self.verify("unknown", "unknown"), 0)
