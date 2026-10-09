#!/usr/bin/env python3
"""Fetch pinned Linux tools for the Kubernetes node, independently of host OS."""

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parents[3] / "tools/versions.lock.yaml"


def locked_version(tool, lock=LOCK):
    """Return a tool's version from tools/versions.lock.yaml, without a leading v."""
    import yaml

    return str(yaml.safe_load(lock.read_text())["tools"][tool]["version"]).lstrip("v")


def specifications(arch):
    """Pinned node tools; every version comes from tools/versions.lock.yaml."""
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[arch]
    v = locked_version
    return [
        ("sigstore/cosign", f"v{v('cosign')}", f"cosign-linux-{arch}", ["cosign"]),
        ("oras-project/oras", f"v{v('oras')}", f"oras_{v('oras')}_linux_{arch}.tar.gz", ["oras"]),
        (
            "moby/buildkit",
            f"v{v('buildkit')}",
            f"buildkit-v{v('buildkit')}.linux-{arch}.tar.gz",
            ["buildctl", "buildkitd", "buildkit-runc"],
        ),
        (
            "rootless-containers/rootlesskit",
            f"v{v('rootlesskit')}",
            f"rootlesskit-{machine}.tar.gz",
            ["rootlesskit"],
        ),
        ("mikefarah/yq", f"v{v('yq')}", f"yq_linux_{arch}", ["yq"]),
        ("open-policy-agent/opa", f"v{v('opa')}", f"opa_linux_{arch}_static", ["opa"]),
        ("anchore/syft", f"v{v('syft')}", f"syft_{v('syft')}_linux_{arch}.tar.gz", ["syft"]),
        ("anchore/grype", f"v{v('grype')}", f"grype_{v('grype')}_linux_{arch}.tar.gz", ["grype"]),
    ]


def fetch(url, target):
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)


def download(output, arch):
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "downloads.json"
    records = json.loads(manifest.read_text()) if manifest.exists() else {}
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        for repo, version, asset, names in specifications(arch):
            key = f"{repo}/{version}/{asset}"
            record = records.get(key, {})
            if record and all(
                (output / name).is_file()
                and hashlib.sha256((output / name).read_bytes()).hexdigest() == record.get(name)
                for name in names
            ):
                continue
            metadata = temporary / "release.json"
            fetch(f"https://api.github.com/repos/{repo}/releases/tags/{version}", metadata)
            release = json.loads(metadata.read_text())
            entry = next(item for item in release["assets"] if item["name"] == asset)
            expected = entry.get("digest", "")
            if not expected.startswith("sha256:"):
                raise ValueError(f"Upstream SHA256 digest unavailable for {key}; refusing download")
            archive = temporary / asset
            fetch(entry["browser_download_url"], archive)
            if "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Checksum mismatch for {key}")
            if asset.endswith(".tar.gz"):
                # Extract only named regular binaries; never follow archive paths or links.
                with tarfile.open(archive) as bundle:
                    for name in names:
                        member = next(m for m in bundle if m.isfile() and Path(m.name).name == name)
                        with (
                            bundle.extractfile(member) as source,
                            (output / ("." + name + ".download")).open("wb") as dest,
                        ):
                            shutil.copyfileobj(source, dest)
            else:
                shutil.copyfile(archive, output / ("." + names[0] + ".download"))
            for name in names:
                (output / ("." + name + ".download")).replace(output / name)
                (output / name).chmod(0o555)
            records[key] = {
                name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in names
            }
            manifest.write_text(json.dumps(records, indent=2) + "\n")
            print(f"Installed {key}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    arguments = parser.parse_args()
    download(arguments.output, arguments.arch)
