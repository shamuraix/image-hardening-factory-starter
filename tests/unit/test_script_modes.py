"""Scripts that start with a shebang must be committed executable.

Tekton steps run them by path. A shebang script committed as mode 644 fails at
run time with "Permission denied" (exit 126), which no other check catches.
Sourced libraries under scripts/lib/ are never executed directly and are exempt.
"""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCED = "scripts/lib/"


class ScriptModeTests(unittest.TestCase):
    def test_shebang_scripts_are_executable(self) -> None:
        listing = subprocess.run(
            ["git", "ls-files", "-s", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout
        offenders = []
        for entry in listing.split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            mode = meta.split()[0]
            if mode != "100644" or path.startswith(SOURCED):
                continue
            file = ROOT / path
            if file.is_file() and file.read_bytes()[:2] == b"#!":
                offenders.append(path)
        self.assertEqual(offenders, [], "commit these as mode 755 (git update-index --chmod=+x)")


if __name__ == "__main__":
    unittest.main()
