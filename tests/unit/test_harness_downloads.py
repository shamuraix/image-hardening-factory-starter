import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "harness_downloads", ROOT / "tests/integration/kind/download-tools.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HarnessDownloadsTests(unittest.TestCase):
    def test_native_node_assets_for_both_architectures(self):
        for architecture, rootless in [("amd64", "x86_64"), ("arm64", "aarch64")]:
            assets = [entry[2] for entry in module.specifications(architecture)]
            self.assertIn(f"rootlesskit-{rootless}.tar.gz", assets)
            self.assertIn(f"yq_linux_{architecture}", assets)
            self.assertIn(f"grype_0.118.0_linux_{architecture}.tar.gz", assets)
            self.assertFalse(any("darwin" in asset for asset in assets))

    def test_checksum_failure_preserves_existing_binary_and_cache_is_verified(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            binary = output / "tool"
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

            specifications = [("fixture/repo", "v1", "tool-linux-amd64", ["tool"])]
            with (
                patch.object(module, "specifications", return_value=specifications),
                patch.object(module, "fetch", side_effect=fetch) as mocked,
            ):
                module.download(output, "amd64")
                self.assertEqual(binary.read_bytes(), payload)
                mocked.reset_mock()
                module.download(output, "amd64")
                mocked.assert_not_called()
                (output / "downloads.json").unlink()

                def corrupt(url, target):
                    fetch(url, target)
                    if "api.github.com" not in url:
                        target.write_bytes(b"corrupt")

                mocked.side_effect = corrupt
                with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                    module.download(output, "amd64")
                self.assertEqual(binary.read_bytes(), payload)
