import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


class ReleaseErrorTests(unittest.TestCase):
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
