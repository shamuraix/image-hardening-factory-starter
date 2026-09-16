import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


class ReleaseErrorTests(unittest.TestCase):
    def test_rpm_errors_cannot_be_allowlisted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mock = root / "podman"
            mock.write_text("""#!/usr/bin/env bash
if [[ $1 == info ]]; then echo true; exit 0; fi
printf '%s' "$RPM_OUTPUT"
printf '%s' "$RPM_ERROR" >&2
exit "$RPM_STATUS"
""")
            mock.chmod(0o755)
            allowlist = root / "allowlist"
            allowlist.write_text("/etc/allowed\n")
            cases = [
                (0, "", "", True),
                (1, "S.5......  c /etc/allowed\n", "", True),
                (2, "", "rpm database failed", False),
                (1, "", "", False),
                (1, "S.5......  c /etc/allowed\n", "error: database corrupt", False),
                (1, "S.5......  c /etc/unexpected\n", "", False),
            ]
            for status, output, error, expected in cases:
                with self.subTest(status=status, output=output, error=error):
                    result = subprocess.run(
                        [
                            "bash",
                            "scripts/assert_rpm_integrity.sh",
                            "fixture",
                            str(root / "result"),
                            str(allowlist),
                        ],
                        cwd=ROOT,
                        env={
                            **os.environ,
                            "PATH": str(root) + ":" + os.environ["PATH"],
                            "RPM_STATUS": str(status),
                            "RPM_OUTPUT": output,
                            "RPM_ERROR": error,
                        },
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, expected, result.stderr)

    def test_auth_file_is_private_valid_json(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source scripts/lib/registry_auth.sh; factory_registry_auth "$1/auth"',
                    "test",
                    str(root),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual((root / "auth/config.json").read_text(), '{"auths":{}}\n')
            self.assertEqual((root / "auth/config.json").stat().st_mode & 0o777, 0o600)
