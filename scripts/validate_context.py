#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import yaml


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    catalog = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
    context = Path(sys.argv[2])
    containerfile = (context / catalog["source"]["containerfile"]).read_text(encoding="utf-8")
    manifest = yaml.safe_load(
        (context / catalog["source"]["hardeningManifest"]).read_text(encoding="utf-8")
    )
    version = str(catalog["product"]["version"])
    if str(manifest["tags"][0]) != version:
        fail(f"manifest primary tag {manifest['tags'][0]} does not match catalog version {version}")
    if catalog["build"].get("buildArgs") and version not in containerfile:
        fail(f"containerfile does not contain catalog version {version}")
    if "registry1.dso.mil" in containerfile or "registry.access.redhat.com" in containerfile:
        fail("containerfile contains a forbidden public/Registry1 base reference")
    if "FROM ${BASE_REF}" not in containerfile:
        fail("containerfile must consume the pipeline-supplied BASE_REF")
    for key, value in catalog["build"].get("buildArgs", {}).items():
        if f"ARG {key}={value}" not in containerfile:
            fail(f"containerfile argument {key} does not match catalog value {value}")
    if catalog["build"]["base"]["kind"] == "catalog":
        base_name = catalog["build"]["base"]["image"]
        base_catalog = yaml.safe_load(Path(f"catalog/images/{base_name}.yaml").read_text())
        expected = str(base_catalog["product"]["version"])
        if str(manifest.get("args", {}).get("BASE_TAG")) != expected:
            fail(f"manifest BASE_TAG must match {base_name} version {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
