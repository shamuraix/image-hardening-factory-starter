"""Build tiny OCI image-layout archives for image-check tests (no container tooling)."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

# A layer item: (path, kind, mode, uid, gid, link_or_content)
# kind: "dir" | "file" | "symlink" | "whiteout" | "opaque"
Item = tuple[str, str, int, int, int, "str | bytes"]


def _layer(items: list[Item]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path, kind, mode, uid, gid, extra in items:
            name = path.lstrip("/")
            if kind == "whiteout":
                parent, _, base = name.rpartition("/")
                name = f"{parent}/.wh.{base}" if parent else f".wh.{base}"
                kind, extra = "file", b""
            elif kind == "opaque":
                name = f"{name}/.wh..wh..opq"
                kind, extra = "file", b""
            info = tarfile.TarInfo(name)
            info.mode, info.uid, info.gid = mode, uid, gid
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, str(extra)
                tar.addfile(info)
            else:
                data = extra if isinstance(extra, bytes) else str(extra).encode()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue())


def build(path: Path, config: dict, layers: list[list[Item]]) -> Path:
    blobs: dict[str, bytes] = {}

    def add(data: bytes) -> str:
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        blobs[digest] = data
        return digest

    layer_descriptors = []
    for items in layers:
        data = _layer(items)
        layer_descriptors.append(
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": add(data),
                "size": len(data),
            }
        )
    config_bytes = json.dumps(config).encode()
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": add(config_bytes),
            "size": len(config_bytes),
        },
        "layers": layer_descriptors,
    }
    manifest_bytes = json.dumps(manifest).encode()
    index = {
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": add(manifest_bytes),
                "size": len(manifest_bytes),
            }
        ],
    }
    with tarfile.open(path, "w") as archive:
        files = {
            "oci-layout": json.dumps({"imageLayoutVersion": "1.0.0"}).encode(),
            "index.json": json.dumps(index).encode(),
        }
        for digest, data in blobs.items():
            files[f"blobs/sha256/{digest.split(':', 1)[1]}"] = data
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path
