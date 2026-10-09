"""Product tests run in rootless Podman inside a Kubernetes step container,
which has no /dev/net/tun; rootless network namespaces (pasta, slirp4netns)
cannot be created there, so every container must say which namespace it uses."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestProfileTests(unittest.TestCase):
    def test_every_podman_run_names_its_network_namespace(self) -> None:
        scripts = sorted((ROOT / "tests/profiles").glob("*/test.sh")) + [
            ROOT / "scripts/assert_rpm_integrity.sh"
        ]
        self.assertGreaterEqual(len(scripts), 5)
        for script in scripts:
            text = script.read_text(encoding="utf-8")
            self.assertNotIn("--publish", text, script)
            self.assertNotIn("podman port", text, script)
            for line in re.findall(r"podman run[^\n]*", text):
                self.assertRegex(line, r"--network (none|host)", f"{script}: {line}")
                self.assertRegex(line, r"--platform|platform_args", f"{script}: {line}")
                if "--detach" not in line:
                    self.assertIn("--network none", line, f"{script}: {line}")

    def test_runner_defaults_rootless_podman_to_no_network_namespace(self) -> None:
        conf = (ROOT / "toolchain/containers.conf").read_text(encoding="utf-8")
        self.assertRegex(conf, r'(?m)^netns = "none"$')
        containerfile = (ROOT / "toolchain/Containerfile.factory-runner").read_text(
            encoding="utf-8"
        )
        self.assertIn("toolchain/containers.conf /home/factory/.config/containers/", containerfile)


if __name__ == "__main__":
    unittest.main()
