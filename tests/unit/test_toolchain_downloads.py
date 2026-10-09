"""toolchain/download-tools.py takes every version from tools/versions.lock.yaml
and refuses anything whose checksum does not match upstream."""

import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "download_tools", ROOT / "toolchain/download-tools.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules["download_tools"] = module  # dataclasses need the module registered
spec.loader.exec_module(module)


class ToolchainDownloadTests(unittest.TestCase):
    def test_every_asset_version_comes_from_the_lock(self):
        lock = yaml.safe_load((ROOT / "tools/versions.lock.yaml").read_text())["tools"]
        by_project = {entry["project"]: entry["version"].lstrip("v") for entry in lock.values()}
        for arch in ("amd64", "arm64"):
            for asset in module.specifications(arch):
                pinned = by_project[f"https://github.com/{asset.repo}"]
                self.assertIn(pinned, asset.tag, asset.repo)
                self.assertFalse("darwin" in asset.asset or "windows" in asset.asset)
        amd = {a.repo: a.asset for a in module.specifications("amd64")}
        self.assertEqual(
            amd["aquasecurity/trivy"],
            f"trivy_{by_project['https://github.com/aquasecurity/trivy']}_Linux-64bit.tar.gz",
        )
        self.assertTrue(amd["Cisco-Talos/clamav"].endswith(".linux.x86_64.rpm"))

    def test_checksum_failure_preserves_existing_binary_and_cache_is_verified(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            binary = output / "tools/tool"
            binary.parent.mkdir()
            binary.write_bytes(b"old")
            binary.chmod(0o555)
            payload = b"verified tool"

            def fetch(url, target):
                if "api.github.com" in url:
                    target.write_text(
                        json.dumps(
                            {
                                "assets": [
                                    {
                                        "name": "tool-linux-amd64",
                                        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                                        "browser_download_url": "https://example.invalid/tool",
                                    }
                                ]
                            }
                        )
                    )
                else:
                    target.write_bytes(payload)

            assets = [module.Asset("fixture/repo", "v1", "tool-linux-amd64", "binary", ("tool",))]
            with patch.object(module, "fetch", side_effect=fetch) as mocked:
                module.download(output, "amd64", assets)
                self.assertEqual(binary.read_bytes(), payload)
                mocked.reset_mock()
                module.download(output, "amd64", assets)
                mocked.assert_not_called()  # cached copy verified by digest
                binary.chmod(0o644)
                binary.write_bytes(b"tampered")
                module.download(output, "amd64", assets)
                self.assertEqual(binary.read_bytes(), payload)

            def bad_fetch(url, target):
                fetch(url, target)
                if "api.github.com" not in url:
                    target.write_bytes(b"not the payload")

            binary.chmod(0o644)
            binary.write_bytes(b"stale")
            with (
                patch.object(module, "fetch", side_effect=bad_fetch),
                self.assertRaisesRegex(ValueError, "Checksum mismatch"),
            ):
                module.download(output, "amd64", assets)
            # A failed download never replaces what is on disk.
            self.assertEqual(binary.read_bytes(), b"stale")
            self.assertEqual(list(output.glob("tools/.*.download")), [])

    def test_missing_upstream_digest_is_refused(self):
        with TemporaryDirectory() as directory:

            def fetch(url, target):
                target.write_text(
                    json.dumps({"assets": [{"name": "x", "browser_download_url": "u"}]})
                )

            assets = [module.Asset("fixture/repo", "v1", "x", "binary", ("x",))]
            with (
                patch.object(module, "fetch", side_effect=fetch),
                self.assertRaisesRegex(ValueError, "digest unavailable"),
            ):
                module.download(Path(directory), "amd64", assets)

    def test_claude_binary_is_checked_against_its_manifest(self):
        with TemporaryDirectory() as directory:
            payload = b"claude"

            def fetch(url, target):
                if url.endswith("manifest.json"):
                    target.write_text(
                        json.dumps(
                            {
                                "version": "9.9.9",
                                "platforms": {
                                    "linux-x64": {"checksum": hashlib.sha256(payload).hexdigest()}
                                },
                            }
                        )
                    )
                else:
                    target.write_bytes(b"wrong")

            with patch.object(module, "fetch", side_effect=fetch):
                with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                    module.download_claude(Path(directory), "amd64", "9.9.9")
                with self.assertRaisesRegex(ValueError, "manifest is for"):
                    module.download_claude(Path(directory), "amd64", "1.0.0")


if __name__ == "__main__":
    unittest.main()
