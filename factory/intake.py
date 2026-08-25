from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


class IntakeError(RuntimeError):
    """Raised when an upstream resource cannot be safely locked."""


def digest_file(path: Path, algorithm: str) -> str:
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise IntakeError(f"unsupported digest algorithm: {algorithm}") from exc
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url: str, destination: Path) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise IntakeError(f"only HTTPS file resources are allowed: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "image-hardening-factory/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)


def _lock_http(resource: dict[str, Any], cache_dir: Path) -> dict[str, Any]:
    filename = resource.get("filename")
    validation = resource.get("validation", {})
    algorithm = validation.get("type")
    expected = str(validation.get("value", "")).lower()
    if not filename or algorithm not in {"sha256", "sha512"} or not expected:
        raise IntakeError("HTTP resource requires filename and sha256/sha512 validation")
    if not isinstance(filename, str) or Path(filename).name != filename or filename in {".", ".."}:
        raise IntakeError(f"HTTP resource filename must be a plain filename: {filename!r}")
    destination = cache_dir / filename
    download_file(resource["url"], destination)
    actual = digest_file(destination, algorithm)
    if actual != expected:
        destination.unlink(missing_ok=True)
        raise IntakeError(f"checksum mismatch for {filename}: expected {expected}, got {actual}")
    sha256 = digest_file(destination, "sha256")
    return {
        "kind": "file",
        "source": resource["url"],
        "filename": filename,
        "size": destination.stat().st_size,
        "declaredDigest": f"{algorithm}:{expected}",
        "contentDigest": f"sha256:{sha256}",
        "artifactoryPath": f"sha256/{sha256}/{filename}",
    }


def _lock_oci(resource: dict[str, Any], cache_dir: Path) -> dict[str, Any]:
    source = resource["url"]
    if "@sha256:" not in source:
        raise IntakeError(f"OCI resource must be pinned by digest: {source}")
    tag = resource.get("tag")
    if not tag:
        raise IntakeError(f"OCI resource requires a local tag: {source}")
    expected = source.rsplit("@", 1)[1]
    output = cache_dir / (tag.replace("/", "-").replace(":", "-") + ".oci.tar")
    subprocess.run(
        [
            "skopeo",
            "copy",
            "--retry-times",
            "5",
            "--remove-signatures",
            source,
            f"oci-archive:{output}:{tag}",
        ],
        check=True,
    )
    raw = subprocess.check_output(["skopeo", "inspect", "--raw", source])
    observed = "sha256:" + hashlib.sha256(raw).hexdigest()
    if observed != expected:
        output.unlink(missing_ok=True)
        raise IntakeError(
            f"OCI manifest digest mismatch for {source}: expected {expected}, got {observed}"
        )
    return {
        "kind": "oci",
        "source": source,
        "tag": tag,
        "declaredDigest": expected,
        "observedManifestDigest": observed,
        "archiveDigest": f"sha256:{digest_file(output, 'sha256')}",
        "artifactoryPath": f"oci-upstream-cache/{expected}",
    }


def resolve_manifest(
    source_dir: str | Path,
    manifest_name: str,
    source_revision: str,
    output: str | Path,
    cache_dir: str | Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    source_dir = Path(source_dir)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = source_dir / manifest_name
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    locked = []
    for resource in manifest.get("resources", []):
        url = resource.get("url") or (resource.get("urls") or [None])[0]
        if not url:
            raise IntakeError("resource is missing url/urls")
        if url.startswith("docker://"):
            locked.append(_lock_oci({**resource, "url": url}, cache_dir))
        else:
            locked.append(_lock_http({**resource, "url": url}, cache_dir))
    lock = {
        "schemaVersion": "1.0",
        "generatedAt": (generated_at or datetime.now(UTC)).isoformat(),
        "source": {
            "revision": source_revision,
            "hardeningManifest": manifest_name,
            "hardeningManifestDigest": f"sha256:{digest_file(manifest_path, 'sha256')}",
        },
        "resources": sorted(
            locked, key=lambda item: (item["kind"], item.get("filename", item.get("tag", "")))
        ),
    }
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(lock, indent=2, sort_keys=True) + "\n"
    output_path.write_text(canonical, encoding="utf-8")
    return lock


def upload_locked_files(lock: dict[str, Any], cache_dir: str | Path, repository_url: str) -> None:
    token = os.environ.get("ARTIFACTORY_WRITE_TOKEN")
    if not token:
        raise IntakeError("ARTIFACTORY_WRITE_TOKEN is required for intake upload")
    cache_dir = Path(cache_dir)
    for resource in lock["resources"]:
        if resource["kind"] != "file":
            continue
        path = cache_dir / resource["filename"]
        target = repository_url.rstrip("/") + "/" + resource["artifactoryPath"]
        request = urllib.request.Request(
            target,
            data=path.read_bytes(),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Checksum-Sha256": resource["contentDigest"].split(":", 1)[1],
            },
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status not in {200, 201}:
                raise IntakeError(
                    f"Artifactory upload failed for {path.name}: HTTP {response.status}"
                )


def temporary_cache() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="factory-intake-")
