"""Helpers for asserting properties of the Tekton definitions in .tekton/."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEKTON = ROOT / ".tekton"


@cache
def documents(kind: str) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(TEKTON.rglob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if document and document.get("kind") == kind:
                found[document["metadata"]["name"]] = document
    return found


def task(name: str) -> dict[str, Any]:
    return documents("Task")[name]


def pipeline(name: str) -> dict[str, Any]:
    return documents("Pipeline")[name]


def pipeline_runs() -> dict[str, dict[str, Any]]:
    return documents("PipelineRun")


def pipeline_task(pipeline_name: str, task_name: str) -> dict[str, Any]:
    spec = pipeline(pipeline_name)["spec"]
    for entry in spec.get("tasks", []) + spec.get("finally", []):
        if entry["name"] == task_name:
            return entry
    raise KeyError(f"{pipeline_name} has no task {task_name}")


def param(entry: dict[str, Any], name: str) -> Any:
    for item in entry.get("params", []):
        if item["name"] == name:
            return item["value"]
    raise KeyError(f"{entry['name']} has no param {name}")


def step(task_name: str, step_name: str) -> dict[str, Any]:
    for item in task(task_name)["spec"]["steps"]:
        if item["name"] == step_name:
            return item
    raise KeyError(f"{task_name} has no step {step_name}")


def seal_inputs(pipeline_name: str, task_name: str) -> set[str]:
    return {
        item.split("=", 1)[0] for item in param(pipeline_task(pipeline_name, task_name), "inputs")
    }


def secret_names(task_name: str) -> set[str]:
    """Every Secret a Task can reference (env secretKeyRef and secret volumes)."""
    spec = task(task_name)["spec"]
    names = set()
    containers = list(spec["steps"])
    if spec.get("stepTemplate"):
        containers.append(spec["stepTemplate"])
    for container in containers:
        for env in container.get("env", []):
            reference = env.get("valueFrom", {}).get("secretKeyRef")
            if reference:
                names.add(reference["name"])
    for volume in spec.get("volumes", []):
        if "secret" in volume:
            names.add(volume["secret"]["secretName"])
    return names
