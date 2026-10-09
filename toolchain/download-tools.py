#!/usr/bin/env python3
"""Download the pinned tools the runner images are built from.

Every version comes from tools/versions.lock.yaml. GitHub release assets are
checked against the SHA-256 digest GitHub records for the asset; the Claude Code
binary is checked against its release manifest. Nothing is fetched at image run
time. Output layout (``dist/`` by default):

  tools/   static binaries copied to /usr/local/bin
  rpms/    packages installed with rpm (ClamAV)
  src/     source archives compiled inside the runner image (shadow, for the
           libcap-aware newuidmap/newgidmap)
  scap/    ComplianceAsCode datastreams for the intake runner
  claude/  the Claude Code binary for the agent runner
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "tools/versions.lock.yaml"
CLAUDE_RELEASES = (
    "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819"
    "/claude-code-releases"
)
MACHINE = {"amd64": "x86_64", "arm64": "aarch64"}
DATASTREAMS = ("ssg-rhel9-ds.xml", "ssg-rhel10-ds.xml")


@dataclass(frozen=True)
class Asset:
    """One GitHub release asset and what to take out of it."""

    repo: str
    tag: str
    asset: str
    kind: str  # binary | tar | rpm | source | scap-zip
    names: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.repo}/{self.tag}/{self.asset}"

    @property
    def subdir(self) -> str:
        return {
            "binary": "tools",
            "tar": "tools",
            "rpm": "rpms",
            "source": "src",
            "scap-zip": "scap",
        }[self.kind]

    @property
    def outputs(self) -> tuple[str, ...]:
        if self.kind in ("rpm", "source"):
            return (self.asset,)
        if self.kind == "scap-zip":
            return DATASTREAMS
        return self.names


def locked_versions(lock: Path = LOCK) -> dict[str, str]:
    tools = yaml.safe_load(lock.read_text(encoding="utf-8"))["tools"]
    return {name: str(entry["version"]).lstrip("v") for name, entry in tools.items()}


def specifications(arch: str, versions: dict[str, str] | None = None) -> list[Asset]:
    """Assets for one architecture, every version taken from the lock."""
    v = versions or locked_versions()
    machine = MACHINE[arch]
    trivy_arch = {"amd64": "64bit", "arm64": "ARM64"}[arch]
    return [
        Asset("sigstore/cosign", f"v{v['cosign']}", f"cosign-linux-{arch}", "binary", ("cosign",)),
        Asset(
            "oras-project/oras",
            f"v{v['oras']}",
            f"oras_{v['oras']}_linux_{arch}.tar.gz",
            "tar",
            ("oras",),
        ),
        Asset(
            "moby/buildkit",
            f"v{v['buildkit']}",
            f"buildkit-v{v['buildkit']}.linux-{arch}.tar.gz",
            "tar",
            ("buildctl", "buildkitd", "buildkit-runc"),
        ),
        Asset(
            "rootless-containers/rootlesskit",
            f"v{v['rootlesskit']}",
            f"rootlesskit-{machine}.tar.gz",
            "tar",
            ("rootlesskit",),
        ),
        Asset("mikefarah/yq", f"v{v['yq']}", f"yq_linux_{arch}", "binary", ("yq",)),
        Asset(
            "open-policy-agent/opa", f"v{v['opa']}", f"opa_linux_{arch}_static", "binary", ("opa",)
        ),
        Asset(
            "anchore/syft",
            f"v{v['syft']}",
            f"syft_{v['syft']}_linux_{arch}.tar.gz",
            "tar",
            ("syft",),
        ),
        Asset(
            "anchore/grype",
            f"v{v['grype']}",
            f"grype_{v['grype']}_linux_{arch}.tar.gz",
            "tar",
            ("grype",),
        ),
        Asset(
            "aquasecurity/trivy",
            f"v{v['trivy']}",
            f"trivy_{v['trivy']}_Linux-{trivy_arch}.tar.gz",
            "tar",
            ("trivy",),
        ),
        Asset(
            "google/osv-scanner",
            f"v{v['osv-scanner']}",
            f"osv-scanner_linux_{arch}",
            "binary",
            ("osv-scanner",),
        ),
        Asset(
            "opencontainers/umoci",
            f"v{v['umoci']}",
            f"umoci.linux.{arch}",
            "binary",
            ("umoci",),
        ),
        Asset(
            "Cisco-Talos/clamav",
            f"clamav-{v['clamav']}",
            f"clamav-{v['clamav']}.linux.{machine}.rpm",
            "rpm",
        ),
        Asset(
            "ComplianceAsCode/content",
            f"v{v['compliance-as-code']}",
            f"scap-security-guide-{v['compliance-as-code']}.zip",
            "scap-zip",
        ),
        # Architecture-independent; compiled by Containerfile.factory-runner.
        Asset("shadow-maint/shadow", v["shadow"], f"shadow-{v['shadow']}.tar.xz", "source"),
    ]


def fetch(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_current(records: dict, key: str, outputs: list[Path]) -> bool:
    record = records.get(key, {})
    return bool(record) and all(
        path.is_file() and sha256(path) == record.get(path.name) for path in outputs
    )


def _extract(asset: Asset, archive: Path, destination: Path) -> None:
    """Write the asset's outputs next to their final names, with a temporary suffix."""
    if asset.kind == "binary":
        shutil.copyfile(archive, destination / f".{asset.names[0]}.download")
    elif asset.kind in ("rpm", "source"):
        shutil.copyfile(archive, destination / f".{asset.asset}.download")
    elif asset.kind == "tar":
        # Extract only the named regular files; never follow archive paths or links.
        with tarfile.open(archive) as bundle:
            for name in asset.names:
                member = next(m for m in bundle if m.isfile() and Path(m.name).name == name)
                with (
                    bundle.extractfile(member) as source,
                    (destination / f".{name}.download").open("wb") as dest,
                ):
                    shutil.copyfileobj(source, dest)
    elif asset.kind == "scap-zip":
        with zipfile.ZipFile(archive) as bundle:
            for name in DATASTREAMS:
                member = next(n for n in bundle.namelist() if Path(n).name == name)
                with (
                    bundle.open(member) as source,
                    (destination / f".{name}.download").open("wb") as dest,
                ):
                    shutil.copyfileobj(source, dest)
    else:
        raise ValueError(f"unknown asset kind {asset.kind}")


def download(output: Path, arch: str, assets: list[Asset] | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "downloads.json"
    records = json.loads(manifest.read_text()) if manifest.exists() else {}
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        for asset in assets if assets is not None else specifications(arch):
            destination = output / asset.subdir
            destination.mkdir(parents=True, exist_ok=True)
            outputs = [destination / name for name in asset.outputs]
            if _is_current(records, asset.key, outputs):
                continue
            metadata = temporary / "release.json"
            fetch(f"https://api.github.com/repos/{asset.repo}/releases/tags/{asset.tag}", metadata)
            release = json.loads(metadata.read_text())
            entry = next(item for item in release["assets"] if item["name"] == asset.asset)
            expected = entry.get("digest", "")
            if not expected.startswith("sha256:"):
                raise ValueError(f"Upstream SHA256 digest unavailable for {asset.key}; refusing")
            archive = temporary / asset.asset
            fetch(entry["browser_download_url"], archive)
            if "sha256:" + sha256(archive) != expected:
                raise ValueError(f"Checksum mismatch for {asset.key}")
            _extract(asset, archive, destination)
            for path in outputs:
                staged = destination / f".{path.name}.download"
                staged.replace(path)
                path.chmod(0o555 if asset.subdir == "tools" else 0o444)
            records[asset.key] = {path.name: sha256(path) for path in outputs}
            manifest.write_text(json.dumps(records, indent=2) + "\n")
            print(f"Installed {asset.key}", flush=True)


def download_claude(output: Path, arch: str, version: str | None = None) -> None:
    """Fetch the Claude Code native binary and verify it against its release manifest."""
    version = version or locked_versions()["claude-code"]
    platform = {"amd64": "linux-x64", "arm64": "linux-arm64"}[arch]
    destination = output / "claude"
    destination.mkdir(parents=True, exist_ok=True)
    binary = destination / "claude"
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / "manifest.json"
        fetch(f"{CLAUDE_RELEASES}/{version}/manifest.json", manifest)
        data = json.loads(manifest.read_text())
        if data.get("version") != version:
            raise ValueError(f"Claude Code manifest is for {data.get('version')}, not {version}")
        expected = data["platforms"][platform]["checksum"]
        if binary.is_file() and sha256(binary) == expected:
            return
        staged = Path(directory) / "claude"
        fetch(f"{CLAUDE_RELEASES}/{version}/{platform}/claude", staged)
        if sha256(staged) != expected:
            raise ValueError("Checksum mismatch for the Claude Code binary")
        shutil.copyfile(staged, binary)
        binary.chmod(0o555)
    print(f"Installed claude-code {version}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?", default=ROOT / "dist")
    parser.add_argument("--arch", choices=sorted(MACHINE), default="amd64")
    parser.add_argument(
        "--with-claude",
        action="store_true",
        help="also fetch the agent runner's Claude Code binary",
    )
    arguments = parser.parse_args()
    download(arguments.output, arguments.arch)
    if arguments.with_claude:
        download_claude(arguments.output, arguments.arch)
