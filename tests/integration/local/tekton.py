#!/usr/bin/env python3
"""Run and inspect a harness PipelineRun: the production validate, prepare
stand-in, context-check, rootless build, SBOM, and image-check Tasks for one
catalog image on the harness cluster. Application images build their catalog
base first in the same run (harness-build-app)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

from factory.tekton import BUILD_APPARMOR_ANNOTATION

ROOT = Path(__file__).resolve().parents[3]
NAMESPACE = "factory-harness"
RUNNER_IMAGE = "localhost/factory-harness-runner:local"
DEFAULT_STATE = Path(".local-factory/harness")

BASE_EXPECTED = {
    "checkout": "True",
    "validate": "True",
    "prepare": "True",
    "context-check": "True",
    "build": "True",
    "sbom": "True",
    "image-check": "True",
    "consume": "True",
    "tamper-detected": "False",
}
APP_EXPECTED = {**BASE_EXPECTED, "base-prepare": "True", "base-build": "True"}
BUILD_TASKS = ("build", "base-build")


def kubectl(state: Path, *args: str) -> str:
    command = ["kubectl", "--kubeconfig", str(state / "kubeconfig"), "-n", NAMESPACE, *args]
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout


def catalog_base(image: str) -> str | None:
    """The catalog base an application image builds on, or None for a base image."""
    path = ROOT / "catalog/images" / f"{image}.yaml"
    if not path.is_file():
        raise SystemExit(f"no catalog entry {path.relative_to(ROOT)}")
    base = yaml.safe_load(path.read_text(encoding="utf-8"))["build"]["base"]
    return base["image"] if base["kind"] == "catalog" else None


def manifest(image: str, timeout: int) -> dict:
    """The PipelineRun, with the pod settings the rendered production runs use:
    the restricted pod security context, no service account token, and the
    unconfined AppArmor annotation on build pods (factory/tekton.py)."""
    base = catalog_base(image)
    params = [
        {"name": "runner-image", "value": RUNNER_IMAGE},
        {"name": "image", "value": image},
    ]
    if base:
        params.append({"name": "base-image", "value": base})
    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {"generateName": f"harness-{image}-"},
        "spec": {
            "pipelineRef": {"name": "harness-build-app" if base else "harness-build"},
            "params": params,
            "taskRunTemplate": {
                "serviceAccountName": "factory-offline",
                "podTemplate": {
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "fsGroup": 10001,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "automountServiceAccountToken": False,
                },
            },
            "taskRunSpecs": [
                {
                    "pipelineTaskName": task,
                    "metadata": {"annotations": {BUILD_APPARMOR_ANNOTATION: "unconfined"}},
                }
                for task in BUILD_TASKS
                if base or task == "build"
            ],
            "timeouts": {"pipeline": f"{timeout}s"},
            "workspaces": [
                {
                    "name": "shared",
                    "volumeClaimTemplate": {
                        "spec": {
                            "accessModes": ["ReadWriteOnce"],
                            # An application run holds the base and the product
                            # (tarballs, two image archives, the build context).
                            "resources": {"requests": {"storage": "40Gi" if base else "20Gi"}},
                        }
                    },
                }
            ],
        },
    }


def run(state: Path, timeout: int, image: str) -> int:
    created = subprocess.run(
        ["kubectl", "--kubeconfig", str(state / "kubeconfig"), "-n", NAMESPACE, "create"]
        + ["-o", "jsonpath={.metadata.name}", "-f", "-"],
        input=json.dumps(manifest(image, timeout)),
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    print(f"created {created}")
    (state / "last-pipelinerun").write_text(created)
    deadline = time.time() + timeout + 120
    while time.time() < deadline:
        status = json.loads(kubectl(state, "get", "pipelinerun", created, "-o", "json"))
        conditions = status.get("status", {}).get("conditions", [])
        if conditions and conditions[0].get("status") in ("True", "False"):
            break
        time.sleep(10)
    else:
        print("timed out waiting for the PipelineRun", file=sys.stderr)
        return 1
    return check(state, created)


def check(state: Path, name: str) -> int:
    pipelinerun = json.loads(kubectl(state, "get", "pipelinerun", name, "-o", "json"))
    condition = next(iter(pipelinerun.get("status", {}).get("conditions", [])), {})
    print(f"pipelinerun {name}: {condition.get('reason')}: {condition.get('message')}")
    expected = (
        APP_EXPECTED
        if pipelinerun["spec"]["pipelineRef"]["name"] == "harness-build-app"
        else BASE_EXPECTED
    )
    runs = json.loads(
        kubectl(state, "get", "taskrun", "-l", f"tekton.dev/pipelineRun={name}", "-o", "json")
    )["items"]
    by_task = {item["metadata"]["labels"]["tekton.dev/pipelineTask"]: item for item in runs}
    failures = []
    for task, want in expected.items():
        got = by_task.get(task, {}).get("status", {}).get("conditions", [{}])[0].get("status")
        print(f"{task}: {got} (want {want})")
        if got != want:
            failures.append(task)
    # tamper-detected must fail in its verify step (the seal check rejecting the
    # wrong digest), not for some other reason such as a missing workspace.
    tamper_steps = by_task.get("tamper-detected", {}).get("status", {}).get("steps", [])
    verify_exit = next(
        (s.get("terminated", {}).get("exitCode") for s in tamper_steps if s["name"] == "verify"),
        None,
    )
    if "tamper-detected" not in failures and verify_exit in (None, 0):
        print(f"tamper-detected: verify step exit code {verify_exit} (want non-zero)")
        failures.append("tamper-detected-verify")
    # Show why the first unexpected failure happened. Tasks that never ran
    # (None) have no pod; the PipelineRun line above says why.
    for task in failures:
        taskrun = by_task.get(task)
        if not taskrun or expected.get(task) == "False":
            continue
        pod = taskrun["status"].get("podName", "")
        if not pod:
            continue
        # The first step that exited non-zero explains the failure; later steps
        # only say "Skipping step because a previous step failed". The run step
        # has onError: continue, so its failure also shows as seal's exit code.
        failed = [
            s["name"]
            for s in taskrun["status"].get("steps", [])
            if s.get("terminated", {}).get("exitCode", 0) != 0
        ]
        step = failed[0] if failed else "run"
        print(f"\n--- {task}: last 60 lines of step-{step} ({pod}); failed steps: {failed} ---")
        try:
            print(kubectl(state, "logs", pod, "-c", f"step-{step}", "--tail=60"))
        except subprocess.CalledProcessError as error:
            print(f"(could not read the log: {error.stderr.strip()})")
        break
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="start a run and wait for it")
    run_parser.add_argument("--timeout", type=int, default=7200)
    run_parser.add_argument("--image", default="ubi10-minimal", help="a catalog image")
    commands.add_parser("status", help="re-check the last run")
    commands.add_parser("log", help="all step logs of the last run")
    args = parser.parse_args()
    if args.command == "run":
        return run(args.state, args.timeout, args.image)
    last = args.state / "last-pipelinerun"
    if not last.is_file():
        raise SystemExit(f"no run recorded in {args.state}; pass the --state you ran with")
    name = last.read_text().strip()
    if args.command == "status":
        return check(args.state, name)
    print(
        kubectl(
            args.state,
            "logs",
            "-l",
            f"tekton.dev/pipelineRun={name}",
            "--all-containers",
            "--prefix",
            "--tail=200",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
