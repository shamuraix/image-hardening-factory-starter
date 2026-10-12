"""Static checks on a built OCI archive. Nothing runs the image.

The image config and the layer tar headers are read directly from
``image.oci.tar`` and merged into one filesystem view (OCI whiteouts and opaque
directories applied), so ownership, modes, symlinks and a few small files
(``/etc/passwd``) can be checked exactly as the container would see them.

Required checks are deterministic defects, such as the wrong user, entrypoint,
ports or volume, a missing runtime, or leftover build artefacts. A failure
fails the stage. Advisory checks record parity with Atlassian's own images
(docker-atlassian-*): install-directory modes, root-owned entrypoint files,
the tools the support scripts call, JAVA_HOME, and Python/Jinja2 parity.
Iron Bank deliberately differs on some of these, so they are evidence only.
"""

from __future__ import annotations

import fnmatch
import gzip
import io
import json
import posixpath
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import yaml

from factory.context_check import ADVISORY, REQUIRED, Report

# Read into memory (small) so /etc/passwd and /etc/group can be parsed.
CONTENT_FILES = {"/etc/passwd", "/etc/group"}
SUPPORT_TOOLS = ("getopt", "su", "top", "nc", "which")
MAX_LINKS = 40


@dataclass
class Entry:
    kind: str  # file | dir | symlink | other
    mode: int
    uid: int
    gid: int
    link: str = ""
    content: bytes | None = None


def _member_path(name: str) -> str:
    """Absolute path for a tar member name ("./etc/x", "etc/x" or "/etc/x")."""
    while name.startswith("./"):
        name = name[2:]
    return posixpath.normpath("/" + name.lstrip("/"))


class ImageFS:
    """Merged view of an image's layers."""

    def __init__(self) -> None:
        self.entries: dict[str, Entry] = {"/": Entry("dir", 0o755, 0, 0)}

    def _remove_lower(self, added: set[str], target: str, include_self: bool) -> None:
        """Delete lower-layer entries at/under target; this layer's additions stay.

        Whiteouts apply only to lower layers (OCI image-spec layer.md), and tar
        member order is not significant, so an entry this layer already added
        must survive a whiteout or opaque marker listed after it.
        """
        prefix = target.rstrip("/") + "/"
        doomed = [
            key
            for key in self.entries
            if key not in added and (key.startswith(prefix) or (include_self and key == target))
        ]
        for key in doomed:
            del self.entries[key]

    def apply_layer(self, layer: tarfile.TarFile) -> None:
        added: set[str] = set()
        for member in layer:
            path = _member_path(member.name)
            parent, name = posixpath.split(path)
            if name == ".wh..wh..opq":
                self._remove_lower(added, parent, include_self=False)
                continue
            if name.startswith(".wh."):
                self._remove_lower(added, posixpath.join(parent, name[4:]), include_self=True)
                continue
            if member.isdir():
                kind = "dir"
            elif member.issym():
                kind = "symlink"
            elif member.isreg() or member.islnk():
                kind = "file"
            else:
                kind = "other"
            if member.islnk():
                linked = self.entries.get(_member_path(member.linkname))
                content = linked.content if linked else None
            else:
                content = None
            if kind == "file" and path in CONTENT_FILES and member.isreg():
                extracted = layer.extractfile(member)
                content = extracted.read() if extracted else b""
            # Replacing a directory with a non-directory removes its children.
            if kind != "dir" and path in self.entries and self.entries[path].kind == "dir":
                self._remove_lower(added, path, include_self=False)
            self.entries[path] = Entry(
                kind, member.mode & 0o7777, member.uid, member.gid, member.linkname, content
            )
            added.add(path)
            # Extraction creates parent directories a layer does not list.
            ancestor = posixpath.dirname(path)
            while ancestor not in self.entries:
                self.entries[ancestor] = Entry("dir", 0o755, 0, 0)
                ancestor = posixpath.dirname(ancestor)

    def apply_layer_stream(self, stream: IO[bytes]) -> None:
        with tarfile.open(fileobj=stream, mode="r|") as layer:
            self.apply_layer(layer)

    def resolve(self, path: str) -> tuple[str, Entry] | None:
        """Follow symlinks (absolute and relative) to the final entry."""
        current = posixpath.normpath(path)
        for _ in range(MAX_LINKS):
            # Resolve symlinked parent directories component by component.
            parts = current.strip("/").split("/")
            resolved = "/"
            restart = False
            for index, part in enumerate(parts):
                candidate = posixpath.join(resolved, part)
                entry = self.entries.get(candidate)
                if entry is None:
                    return None
                if entry.kind == "symlink":
                    target = entry.link
                    base = target if target.startswith("/") else posixpath.join(resolved, target)
                    rest = parts[index + 1 :]
                    current = posixpath.normpath(posixpath.join(base, *rest))
                    restart = True
                    break
                resolved = candidate
            if not restart:
                return resolved, self.entries[resolved]
        return None

    def which(self, command: str, path_env: str) -> str | None:
        for directory in path_env.split(":"):
            found = self.resolve(posixpath.join(directory or "/", command))
            if found and found[1].kind == "file" and found[1].mode & 0o111:
                return posixpath.join(directory, command)
        return None

    def glob(self, pattern: str) -> list[str]:
        return [path for path in self.entries if fnmatch.fnmatchcase(path, pattern)]


def _blob_stream(archive: tarfile.TarFile, digest: str) -> IO[bytes]:
    algorithm, value = digest.split(":", 1)
    member = archive.extractfile(f"blobs/{algorithm}/{value}")
    if member is None:
        raise ValueError(f"blob {digest} missing from archive")
    return member


def _open_layer(archive: tarfile.TarFile, descriptor: dict[str, Any]) -> IO[bytes]:
    """Layer as a decompressed stream; never holds a whole layer in memory."""
    media = descriptor.get("mediaType", "")
    if "zstd" in media:
        raise ValueError("zstd-compressed layers are not supported by image-check")
    raw = io.BufferedReader(_blob_stream(archive, descriptor["digest"]))
    if "gzip" in media or raw.peek(2)[:2] == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=raw)
    return raw


def _open_blob(archive: tarfile.TarFile, digest: str) -> bytes:
    algorithm, value = digest.split(":", 1)
    member = archive.extractfile(f"blobs/{algorithm}/{value}")
    if member is None:
        raise ValueError(f"blob {digest} missing from archive")
    return member.read()


def load_image(path: Path) -> tuple[dict[str, Any], ImageFS]:
    """Image config and merged filesystem from an OCI image-layout tarball."""
    with tarfile.open(path) as archive:
        index = json.loads(archive.extractfile("index.json").read())
        descriptor = index["manifests"][0]
        manifest = json.loads(_open_blob(archive, descriptor["digest"]))
        if "manifests" in manifest:  # nested index (multi-platform)
            manifest = json.loads(_open_blob(archive, manifest["manifests"][0]["digest"]))
        config = json.loads(_open_blob(archive, manifest["config"]["digest"]))
        fs = ImageFS()
        for layer in manifest["layers"]:
            fs.apply_layer_stream(_open_layer(archive, layer))
    return config, fs


def _env(config: dict[str, Any]) -> dict[str, str]:
    return dict(
        item.split("=", 1) for item in config.get("config", {}).get("Env", []) if "=" in item
    )


def _passwd(fs: ImageFS) -> dict[str, tuple[int, int]]:
    entry = fs.entries.get("/etc/passwd")
    users = {}
    for line in (entry.content or b"").decode(errors="replace").splitlines() if entry else []:
        fields = line.split(":")
        if len(fields) >= 4 and fields[2].isdigit():
            users[fields[0]] = (int(fields[2]), int(fields[3]))
    return users


def _writable_by(entry: Entry, uid: int, gid: int) -> bool:
    return bool(
        (entry.uid == uid and entry.mode & stat.S_IWUSR)
        or (entry.gid == gid and entry.mode & stat.S_IWGRP)
        or entry.mode & stat.S_IWOTH
    )


def check_image(
    catalog_path: Path,
    image_path: Path,
    sbom_path: Path | None = None,
    context_result: Path | None = None,
) -> Report:
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    report = Report(catalog["metadata"]["name"])
    verify = catalog.get("verify")
    if not verify:
        report.add("profile", ADVISORY, True, "no verify block; nothing to check")
        return report
    try:
        config, fs = load_image(image_path)
    except (OSError, ValueError, KeyError, tarfile.TarError, json.JSONDecodeError) as error:
        report.add("image-readable", REQUIRED, False, f"{type(error).__name__}: {error}")
        return report
    report.add("image-readable", REQUIRED, True, f"{len(fs.entries)} paths")
    cfg = config.get("config", {})
    env = _env(config)
    path_env = env.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    users = _passwd(fs)
    run_user, run_uid = verify["runUser"], int(verify["runUid"])

    # --- required: runtime identity -------------------------------------
    user = str(cfg.get("User", ""))
    name, _, _group = user.partition(":")
    uid = users.get(name, (None, None))[0] if not name.isdigit() else int(name)
    report.add(
        "user",
        REQUIRED,
        name in (run_user, str(run_uid)) and uid == run_uid and uid != 0,
        f"User={user!r} resolves to uid {uid}; expected {run_user} ({run_uid})",
    )
    report.add(
        "passwd-entry",
        REQUIRED,
        users.get(run_user, (None,))[0] == run_uid,
        f"/etc/passwd {run_user} -> {users.get(run_user)}",
    )
    report.add(
        "env-run-uid",
        REQUIRED,
        env.get("RUN_UID") == str(run_uid) and env.get("RUN_USER") == run_user,
        f"RUN_USER={env.get('RUN_USER')} RUN_UID={env.get('RUN_UID')}",
    )
    report.add(
        "entrypoint",
        REQUIRED,
        cfg.get("Entrypoint") == verify["entrypoint"] and cfg.get("Cmd") == verify["cmd"],
        f"Entrypoint={cfg.get('Entrypoint')} Cmd={cfg.get('Cmd')}",
    )
    exposed = sorted(cfg.get("ExposedPorts", {}) or {})
    expected = sorted(f"{port}/tcp" for port in verify["ports"])
    report.add("exposed-ports", REQUIRED, exposed == expected, f"{exposed}; expected {expected}")
    home = env.get(verify["homeEnv"], "")
    volumes = sorted(cfg.get("Volumes", {}) or {})
    report.add(
        "home-volume",
        REQUIRED,
        bool(home) and home in volumes,
        f"{verify['homeEnv']}={home!r}; Volumes={volumes}",
    )
    home_entry = fs.entries.get(home)
    report.add(
        "home-owner",
        REQUIRED,
        home_entry is not None and home_entry.kind == "dir" and home_entry.uid == run_uid,
        f"{home} owner uid {home_entry.uid if home_entry else None}",
    )

    # --- required: files and runtimes ------------------------------------
    for required in verify.get("requiredPaths", []):
        found = fs.resolve(required)
        report.add(
            f"present:{required}", REQUIRED, found is not None, found[0] if found else "missing"
        )
    for executable in verify["entrypoint"][:1] + verify["cmd"][:1]:
        found = fs.resolve(executable)
        ok = bool(found and found[1].kind == "file" and found[1].mode & 0o111)
        report.add(
            f"executable:{executable}", REQUIRED, ok, oct(found[1].mode) if found else "missing"
        )
    for forbidden in verify.get("forbiddenPaths", []):
        leftovers = fs.glob(forbidden)
        report.add(
            f"absent:{forbidden}", REQUIRED, not leftovers, ", ".join(leftovers[:5]) or "absent"
        )
    for command in ("java", "python3"):
        where = fs.which(command, path_env)
        report.add(
            f"on-path:{command}", REQUIRED, where is not None, where or f"not on PATH {path_env}"
        )
    jinja = fs.glob("/usr/lib/python3*/site-packages/jinja2/__init__.py") + fs.glob(
        "/usr/lib64/python3*/site-packages/jinja2/__init__.py"
    )
    report.add(
        "python-jinja2", REQUIRED, bool(jinja), jinja[0] if jinja else "jinja2 not installed"
    )
    for variable in verify.get("envPaths", []):
        value = env.get(variable, "")
        found = fs.resolve(value) if value else None
        report.add(
            f"env-path:{variable}",
            REQUIRED,
            found is not None,
            f"{value!r} -> {found[0] if found else 'missing'}",
        )

    sbom = _sbom_packages(sbom_path)
    if verify["profile"] == "bitbucket" and sbom is not None:
        from factory.context_check import GIT_DENYLIST

        git_versions = sorted(sbom.get("git", set()) | sbom.get("git-core", set()))
        banned = [v for v in git_versions if ".".join(v.split(".")[:2]) in GIT_DENYLIST]
        report.add(
            "no-banned-rpm-git",
            REQUIRED,
            not banned,
            f"git RPM versions {git_versions or 'none'}; denied {banned or 'none'} "
            f"(Atlassian denies {sorted(GIT_DENYLIST)})",
        )

    # --- advisory: Atlassian image parity --------------------------------
    install = env.get(verify["installDirEnv"], "")
    install_entry = fs.entries.get(install)
    report.add(
        "parity:install-dir",
        ADVISORY,
        bool(install_entry and install_entry.uid == run_uid and install_entry.mode == 0o550),
        f"{install} mode {oct(install_entry.mode) if install_entry else None} uid "
        f"{install_entry.uid if install_entry else None}; Atlassian uses 0550 owned by {run_user}",
    )
    for sub in ("logs", "work", "temp"):
        entry = fs.entries.get(f"{install}/{sub}")
        report.add(
            f"parity:install-{sub}",
            ADVISORY,
            bool(entry and entry.mode == 0o770),
            f"mode {oct(entry.mode) if entry else None}; Atlassian uses 0770",
        )
    run_gid = users.get(run_user, (run_uid, run_uid))[1]
    for path in ("/entrypoint.py", "/entrypoint_helpers.py", "/opt/atlassian/support"):
        entry = fs.entries.get(path)
        if entry:
            report.add(
                f"parity:root-owned:{path}",
                ADVISORY,
                entry.uid == 0 and not _writable_by(entry, run_uid, run_gid),
                f"owner uid {entry.uid}, mode {oct(entry.mode)}; should not be writable by {run_user}",
            )
    missing = [tool for tool in SUPPORT_TOOLS if not fs.which(tool, path_env)]
    report.add(
        "parity:support-tools",
        ADVISORY,
        not missing,
        "missing " + ", ".join(missing) + " (util-linux, procps-ng, nmap-ncat, which)"
        if missing
        else "present",
    )
    java_home = env.get("JAVA_HOME", "")
    jcmd = fs.resolve(f"{java_home}/bin/jcmd") if java_home else None
    report.add(
        "parity:java-home",
        ADVISORY,
        jcmd is not None,
        f"JAVA_HOME={java_home!r}; support scripts call $JAVA_HOME/bin/jcmd",
    )
    if context_result and context_result.exists():
        rendered_with = json.loads(context_result.read_text()).get("environment", {})
        image_jinja = sorted((sbom or {}).get("python3-jinja2", set()))
        tested = rendered_with.get("jinja2", "").split(".")[:2]
        # Every Jinja2 the SBOM saw must match; an older copy in a lower layer
        # still counts, because all-layers scope cannot tell which one is live.
        same = bool(image_jinja) and all(v.split(".")[:2] == tested for v in image_jinja)
        report.add(
            "parity:jinja2",
            ADVISORY,
            same,
            f"templates tested with Jinja2 {rendered_with.get('jinja2')}; "
            f"image SBOM has {image_jinja or 'unknown'}",
        )
    return report


def _sbom_packages(path: Path | None) -> dict[str, set[str]] | None:
    """name -> every version of that RPM in a CycloneDX SBOM.

    The SBOM is generated with ``--scope all-layers``, so one name can appear
    with several versions; keeping only one would let an allowed entry hide a
    denied one.
    """
    if not path or not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    packages: dict[str, set[str]] = {}
    for component in data.get("components", []):
        if str(component.get("purl", "")).startswith("pkg:rpm/"):
            version = str(component.get("version", "")).split(":")[-1]
            packages.setdefault(component.get("name", ""), set()).add(version.split("-")[0])
    return packages
