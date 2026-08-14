from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from factory.schema import validate


class CatalogError(ValueError):
    """Raised when catalog content is invalid or inconsistent."""


@dataclass(frozen=True)
class ImageDefinition:
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return self.data["metadata"]["name"]

    @property
    def base_image(self) -> str | None:
        base = self.data["build"]["base"]
        return base.get("image") if base["kind"] == "catalog" else None

    @property
    def track(self) -> str:
        return self.data["metadata"]["track"]


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "catalog/schema/image.schema.json"


def load_image(path: str | Path, schema_path: str | Path | None = None) -> ImageDefinition:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = json.loads(Path(schema_path or _schema_path()).read_text(encoding="utf-8"))
    errors = validate(data, schema)
    if errors:
        raise CatalogError("\n".join(f"{path}:{error.path}: {error.message}" for error in errors))
    return ImageDefinition(path=path, data=data)


def load_catalog(directory: str | Path) -> dict[str, ImageDefinition]:
    directory = Path(directory)
    images: dict[str, ImageDefinition] = {}
    for path in sorted(directory.glob("*.yaml")):
        image = load_image(path)
        if image.name in images:
            raise CatalogError(f"duplicate catalog image name: {image.name}")
        images[image.name] = image
    if not images:
        raise CatalogError(f"no catalog YAML files found in {directory}")
    validate_relationships(images.values())
    return images


def validate_relationships(images: Iterable[ImageDefinition]) -> None:
    by_name = {image.name: image for image in images}
    for image in by_name.values():
        base = image.base_image
        if base and base not in by_name:
            raise CatalogError(f"{image.name}: unknown catalog base image {base}")
        if base and image.track == "release" and by_name[base].track == "canary":
            raise CatalogError(f"{image.name}: release image cannot depend on canary base {base}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise CatalogError(f"catalog dependency cycle includes {name}")
        if name in visited:
            return
        visiting.add(name)
        base = by_name[name].base_image
        if base:
            visit(base)
        visiting.remove(name)
        visited.add(name)

    for name in by_name:
        visit(name)


def descendants(images: dict[str, ImageDefinition], roots: set[str]) -> set[str]:
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for image in images.values():
            if image.base_image in selected and image.name not in selected:
                selected.add(image.name)
                changed = True
    return selected


def topological_order(images: dict[str, ImageDefinition], selected: set[str]) -> list[str]:
    result: list[str] = []
    remaining = set(selected)
    while remaining:
        ready = sorted(
            name
            for name in remaining
            if images[name].base_image is None or images[name].base_image not in remaining
        )
        if not ready:
            raise CatalogError("unable to order selected images")
        result.extend(ready)
        remaining.difference_update(ready)
    return result
