#!/usr/bin/env python3
"""Run and inspect the harness-build PipelineRun: a real rootless build, SBOM, and
test of a catalog base image on the disposable kind cluster."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

NAMESPACE = "factory-harness"
RUNNER_IMAGE = "localhost/factory-review-runner:review"


def kubectl(state: Path, *args: str, capture: bool = True) -> str:
    command = ["kubectl", "--kubeconfig", str(state / "kubeconfig"), "-n", NAMESPACE, *args]
    result = subprocess.run(command, check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


EXPECTED = {
    "checkout": "True",
    "validate": "True",
    "prepare": "True",
    "build": "True",
    "sbom": "True",
    "test": "True",
    "consume": "True",
    "tamper-detected": "False",
}


def pod_template(state: Path, stage: str) -> dict:
    """Same shape factory/tekton.py renders for build and test, plus the crun
    RuntimeClass that probe-crun.py recorded in rootful mode."""
    template: dict = {}
    if stage in ("build", "test"):
        template["hostUsers"] = False
        runtime_class = state / "runtime-class"
        if runtime_class.exists():
            name = runtime_class.read_text().strip()
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
                raise ValueError("Invalid saved harness RuntimeClass name")
            template["runtimeClassName"] = name
    return template


def run(state: Path, timeout: int, image: str) -> int:
    manifest = {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {"generateName": "harness-build-"},
        "spec": {
            "pipelineRef": {"name": "harness-build"},
            "params": [
                {"name": "runner-image", "value": RUNNER_IMAGE},
                {"name": "image", "value": image},
            ],
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
                {"pipelineTaskName": stage, "podTemplate": pod_template(state, stage)}
                for stage in ("build", "test")
            ],
            "timeouts": {"pipeline": f"{timeout}s"},
            "workspaces": [
                {
                    "name": "shared",
                    "volumeClaimTemplate": {
                        "spec": {
                            "accessModes": ["ReadWriteOnce"],
                            "resources": {"requests": {"storage": "20Gi"}},
                        }
                    },
                }
            ],
        },
    }
    created = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(state / "kubeconfig"),
            "-n",
            NAMESPACE,
            "create",
            "-o",
            "jsonpath={.metadata.name}",
            "-f",
            "-",
        ],
        input=json.dumps(manifest),
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
        time.sleep(5)
    else:
        print("timed out waiting for the PipelineRun", file=sys.stderr)
        return 1
    return check(state, created)


def check(state: Path, name: str) -> int:
    pipelinerun = json.loads(kubectl(state, "get", "pipelinerun", name, "-o", "json"))
    condition = next(iter(pipelinerun.get("status", {}).get("conditions", [])), {})
    print(f"pipelinerun {name}: {condition.get('reason')}: {condition.get('message')}")
    runs = json.loads(
        kubectl(state, "get", "taskrun", "-l", f"tekton.dev/pipelineRun={name}", "-o", "json")
    )["items"]
    statuses = {
        item["metadata"]["labels"]["tekton.dev/pipelineTask"]: item["status"]["conditions"][0]
        for item in runs
    }
    failures = []
    for task, want in EXPECTED.items():
        got = statuses.get(task, {}).get("status")
        print(f"{task}: {got} (want {want})")
        if got != want:
            failures.append(task)
    # tamper-detected must fail in its verify step (the seal check rejecting the
    # wrong digest), not for some other reason such as a missing workspace.
    tamper_steps = next(
        (
            item["status"].get("steps", [])
            for item in runs
            if item["metadata"]["labels"]["tekton.dev/pipelineTask"] == "tamper-detected"
        ),
        [],
    )
    verify_exit = next(
        (
            s.get("terminated", {}).get("exitCode")
            for s in tamper_steps
            if s.get("name") == "verify"
        ),
        None,
    )
    if "tamper-detected" not in failures and verify_exit in (None, 0):
        print(f"tamper-detected: verify step exit code {verify_exit} (want non-zero)")
        failures.append("tamper-detected-verify")
    seal = next(
        (
            r["value"]
            for item in runs
            if item["metadata"]["labels"]["tekton.dev/pipelineTask"] == "validate"
            for r in item["status"].get("results", [])
            if r["name"] == "seal"
        ),
        "",
    )
    print(f"validate seal: {seal or 'missing'}")
    if len(seal) != 64:
        failures.append("validate-seal")
    # Show why the first unexpected failure happened, so nobody has to find the
    # pod and step by hand. Skipped tasks (None) have no pod.
    for task in failures:
        if statuses.get(task, {}).get("status") != "False" or EXPECTED.get(task) == "False":
            continue
        pod = next(
            item["status"].get("podName", "")
            for item in runs
            if item["metadata"]["labels"]["tekton.dev/pipelineTask"] == task
        )
        if not pod:
            continue
        print(f"\n--- {task}: last 60 lines of step-run ({pod}) ---")
        try:
            print(kubectl(state, "logs", pod, "-c", "step-run", "--tail=60"))
        except subprocess.CalledProcessError as error:
            print(f"(could not read the log: {error.stderr.strip()})")
        break
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path(".local-factory/kind-review"))
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--timeout", type=int, default=3600)
    run_parser.add_argument("--image", default="ubi9-minimal", help="a catalog base image")
    commands.add_parser("status")
    commands.add_parser("log")
    args = parser.parse_args()
    if args.command == "run":
        return run(args.state, args.timeout, args.image)
    name = (args.state / "last-pipelinerun").read_text().strip()
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
