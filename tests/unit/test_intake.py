import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import yaml

from factory import intake


class IntakeTests(unittest.TestCase):
    def test_http_resource_is_checksum_verified_and_locked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cache = root / "source", root / "cache"
            source.mkdir()
            payload = b"known-resource\n"
            import hashlib

            digest = hashlib.sha512(payload).hexdigest()
            manifest = {
                "resources": [
                    {
                        "filename": "resource.bin",
                        "url": "https://upstream.example/resource.bin",
                        "validation": {"type": "sha512", "value": digest},
                    }
                ]
            }
            (source / "hardening_manifest.yaml").write_text(
                yaml.safe_dump(manifest), encoding="utf-8"
            )

            def fake_download(_url: str, destination: Path) -> None:
                destination.write_bytes(payload)

            output = root / "resource-lock.json"
            with patch.object(intake, "download_file", side_effect=fake_download):
                lock = intake.resolve_manifest(
                    source,
                    "hardening_manifest.yaml",
                    "a" * 40,
                    output,
                    cache,
                    generated_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            self.assertEqual(lock["resources"][0]["declaredDigest"], f"sha512:{digest}")
            self.assertTrue(lock["resources"][0]["contentDigest"].startswith("sha256:"))
            self.assertTrue(output.exists())

    def test_http_resource_rejects_path_traversal_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cache = root / "source", root / "cache"
            source.mkdir()
            manifest = {
                "resources": [
                    {
                        "filename": "../outside.bin",
                        "url": "https://upstream.example/resource.bin",
                        "validation": {"type": "sha256", "value": "0" * 64},
                    }
                ]
            }
            (source / "hardening_manifest.yaml").write_text(
                yaml.safe_dump(manifest), encoding="utf-8"
            )

            with self.assertRaisesRegex(intake.IntakeError, "plain filename"):
                intake.resolve_manifest(
                    source,
                    "hardening_manifest.yaml",
                    "a" * 40,
                    root / "resource-lock.json",
                    cache,
                )

    def test_oci_resource_rejects_manifest_digest_mismatch(self) -> None:
        resource = {
            "url": f"docker://registry.example/image@sha256:{'0' * 64}",
            "tag": "image:test",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(intake.subprocess, "run"),
            patch.object(intake.subprocess, "check_output", return_value=b"different manifest"),
            self.assertRaisesRegex(intake.IntakeError, "OCI manifest digest mismatch"),
        ):
            intake._lock_oci(resource, Path(directory))
