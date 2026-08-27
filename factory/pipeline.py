from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.catalog import ImageDefinition, descendants, topological_order

PLAN_SCHEMA_VERSION = 1


def dependency_waves(images: dict[str, ImageDefinition], selected: set[str]) -> list[list[str]]:
    waves: list[list[str]] = []
    remaining = set(selected)
    while remaining:
        ready = sorted(
            name
            for name in remaining
            if images[name].base_image is None or images[name].base_image not in remaining
        )
        if not ready:
            raise ValueError("unable to order selected images")
        waves.append(ready)
        remaining.difference_update(ready)
    return waves


def render_plan(
    images: dict[str, ImageDefinition], roots: set[str] | None = None
) -> dict[str, Any]:
    roots = set(images) if roots is None else roots
    unknown = roots.difference(images)
    if unknown:
        raise ValueError(f"unknown changed images: {', '.join(sorted(unknown))}")
    selected = descendants(images, roots)
    ordered = topological_order(images, selected)
    return {
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "images": [
            {
                "name": name,
                "catalogFile": images[name].path.as_posix(),
                "track": images[name].track,
                "baseImage": images[name].base_image,
                "dependsOn": (
                    [images[name].base_image] if images[name].base_image in selected else []
                ),
            }
            for name in ordered
        ],
        "waves": dependency_waves(images, selected),
    }


def write_plan(document: dict[str, Any], output: str | Path) -> None:
    Path(output).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
