import hashlib
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

    def test_http_resource_creates_parent_directories_for_output(self) -> None:
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

            output = root / "nested" / "locks" / "resource-lock.json"
            with patch.object(intake, "download_file", return_value=None) as fake_download:
                fake_download.side_effect = lambda _url, destination: destination.write_bytes(payload)
                intake.resolve_manifest(
                    source,
                    "hardening_manifest.yaml",
                    "a" * 40,
                    output,
                    cache,
                    generated_at=datetime(2026, 1, 1, tzinfo=UTC),
                )

            self.assertTrue(output.exists())
            self.assertEqual(output.parent.exists(), True)

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

    def test_oci_copy_removes_transport_signatures_and_retries(self) -> None:
        resource = {
            "url": "docker://registry.example.test/image@sha256:"
            + "a" * 64,
            "tag": "image:test",
        }
        raw_manifest = b"manifest"
        resource["url"] = (
            "docker://registry.example.test/image@sha256:"
            + hashlib.sha256(raw_manifest).hexdigest()
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(intake.subprocess, "run") as run,
            patch.object(intake.subprocess, "check_output", return_value=raw_manifest),
            patch.object(intake, "digest_file", return_value="b" * 64),
        ):
            intake._lock_oci(resource, Path(directory))

        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["skopeo", "copy"])
        self.assertIn("--retry-times", command)
        self.assertIn("--remove-signatures", command)
