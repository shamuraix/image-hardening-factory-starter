#!/usr/bin/env python3
"""Run and inspect the harness smoke PipelineRun for the Tekton harness."""

from __future__ import annotations

import argparse
import json
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


def run(state: Path, timeout: int) -> int:
    manifest = {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {"generateName": "harness-smoke-"},
        "spec": {
            "pipelineRef": {"name": "harness-smoke"},
            "params": [{"name": "runner-image", "value": RUNNER_IMAGE}],
            "taskRunTemplate": {
                "serviceAccountName": "factory-offline",
                "podTemplate": {
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "fsGroup": 10001}
                },
            },
            "workspaces": [
                {
                    "name": "shared",
                    "volumeClaimTemplate": {
                        "spec": {
                            "accessModes": ["ReadWriteOnce"],
                            "resources": {"requests": {"storage": "2Gi"}},
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
    deadline = time.time() + timeout
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
    runs = json.loads(
        kubectl(state, "get", "taskrun", "-l", f"tekton.dev/pipelineRun={name}", "-o", "json")
    )["items"]
    statuses = {
        item["metadata"]["labels"]["tekton.dev/pipelineTask"]: item["status"]["conditions"][0]
        for item in runs
    }
    expected = {
        "checkout": "True",
        "validate": "True",
        "consume": "True",
        "tamper-detected": "False",
    }
    failures = []
    for task, want in expected.items():
        got = statuses.get(task, {}).get("status")
        print(f"{task}: {got} (want {want})")
        if got != want:
            failures.append(task)
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
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path(".local-factory/kind-review"))
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--timeout", type=int, default=900)
    commands.add_parser("status")
    commands.add_parser("log")
    args = parser.parse_args()
    if args.command == "run":
        return run(args.state, args.timeout)
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
