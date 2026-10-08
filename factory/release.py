"""Release requests: the reviewed, version-controlled trigger for signing and
promotion.

A release request is ``releases/<environment>/<image>.yaml``. The build
pipeline writes it (deterministically, from sealed evidence) on a branch and
opens a change request; humans approve and merge; the merge triggers the
release pipeline. The file at HEAD is therefore always the most recent release
requested for that environment, and git history is the release log.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from factory.catalog import load_catalog
from factory.schema import validate

RELEASES_DIRECTORY = "releases"
ENVIRONMENTS = ("commercial", "gov1", "gov2")


class ReleaseRequestError(ValueError):
    """Raised when a release request is malformed or inconsistent."""


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "schemas/release-request.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def request_path(environment: str, image: str) -> str:
    if environment not in ENVIRONMENTS:
        raise ReleaseRequestError(f"unknown release environment: {environment}")
    return f"{RELEASES_DIRECTORY}/{environment}/{image}.yaml"


def load_request(
    path: str | Path, catalog_dir: str | Path, *, text: str | None = None
) -> dict[str, Any]:
    path = PurePosixPath(str(path))
    data = yaml.safe_load(text if text is not None else Path(str(path)).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ReleaseRequestError(f"{path}: release request must be a mapping")
    errors = validate(data, _schema())
    if errors:
        raise ReleaseRequestError(
            "\n".join(f"{path}:{error.path}: {error.message}" for error in errors)
        )
    parts = path.parts[-3:]
    if len(parts) != 3 or parts[0] != RELEASES_DIRECTORY:
        raise ReleaseRequestError(f"{path}: must live at releases/<environment>/<image>.yaml")
    environment, filename = parts[1], parts[2]
    image = data["metadata"]["image"]
    if data["metadata"]["environment"] != environment:
        raise ReleaseRequestError(f"{path}: metadata.environment must be {environment}")
    if filename != f"{image}.yaml":
        raise ReleaseRequestError(f"{path}: file name must be {image}.yaml")
    images = load_catalog(catalog_dir)
    if image not in images:
        raise ReleaseRequestError(f"{path}: unknown catalog image {image}")
    catalog = images[image].data
    image_path = catalog["publication"]["imagePath"]
    if not data["candidate"]["repository"].endswith("/" + image_path):
        raise ReleaseRequestError(f"{path}: candidate repository must end with /{image_path}")
    if images[image].track == "canary" and environment != "commercial":
        raise ReleaseRequestError(f"{path}: canary images release only to commercial")
    return data


def changed_requests(repo: str | Path, commit: str) -> list[str]:
    """Release request files added or modified by ``commit`` relative to its first parent."""
    output = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=AM",
            "-z",
            f"{commit}^1",
            commit,
            "--",
            f"{RELEASES_DIRECTORY}/",
        ],
        cwd=repo,
    ).decode()
    return sorted(name for name in output.split("\0") if name.endswith(".yaml"))


def resolve(repo: str | Path, catalog_dir: str | Path, commit: str) -> dict[str, Any]:
    names = changed_requests(repo, commit)
    if len(names) != 1:
        raise ReleaseRequestError(
            f"a release merge must change exactly one release request; found {len(names)}: "
            + ", ".join(names)
        )
    return load_request(Path(repo) / names[0], Path(repo) / catalog_dir)


def build_request(
    work_dir: str | Path, catalog_file: str | Path, environment: str, pipeline_run: str = ""
) -> dict[str, Any]:
    """Build a release request from sealed build evidence (no model involvement)."""
    work_dir = Path(work_dir)
    catalog = yaml.safe_load(Path(catalog_file).read_text(encoding="utf-8"))
    metadata = json.loads((work_dir / "image-metadata.json").read_text(encoding="utf-8"))
    imported = json.loads((work_dir / "import-result.json").read_text(encoding="utf-8"))
    bundle = json.loads((work_dir / "evidence-bundle.json").read_text(encoding="utf-8"))
    gate = json.loads((work_dir / "evidence/gate-result.json").read_text(encoding="utf-8"))
    if gate.get("allow") is not True:
        raise ReleaseRequestError("cannot request release for a candidate the gate denied")
    if not imported.get("imported") or imported.get("digest") != metadata["digest"]:
        raise ReleaseRequestError("quarantine import does not match the built candidate")
    reference = imported["imageRef"]
    repository, _, tag = reference.rpartition(":")
    if "/" in tag or not repository:
        raise ReleaseRequestError(f"quarantine reference has no tag: {reference}")
    return {
        "apiVersion": "image-hardening-factory/v1alpha1",
        "kind": "ReleaseRequest",
        "metadata": {"image": catalog["metadata"]["name"], "environment": environment},
        "candidate": {
            "repository": repository,
            "tag": tag,
            "digest": metadata["digest"],
            "productVersion": str(catalog["product"]["version"]),
            "sourceRevision": metadata["sourceRevision"],
            "factoryRevision": metadata["factoryRevision"],
            "pipelineRun": pipeline_run,
        },
        "evidence": {"manifestDigest": bundle["manifestDigest"], "sha256": bundle["sha256"]},
        "gate": {"allow": True, "warnings": list(gate.get("warn", []))},
    }


def dump_request(request: dict[str, Any]) -> str:
    return (
        "# Release request. Merging this file signs and promotes the candidate below.\n"
        "# Review the attached evidence summary before approving.\n"
        + yaml.safe_dump(request, sort_keys=False, width=100)
    )
