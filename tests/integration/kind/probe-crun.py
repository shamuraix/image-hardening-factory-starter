#!/usr/bin/env python3
"""Test a separate crun RuntimeClass on the dedicated rootful kind node."""

import argparse
import fcntl
import json
import re
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path


def crun_config(text, handler):
    config = tomllib.loads(text)
    plugin = {2: "io.containerd.grpc.v1.cri", 3: "io.containerd.cri.v1.runtime"}.get(
        config.get("version")
    )
    if not plugin:
        raise ValueError("Only containerd config versions 2 and 3 are supported")
    runtimes = config["plugins"][plugin]["containerd"]["runtimes"]
    if handler in runtimes:
        raise ValueError("Runtime handler already exists; refusing to overwrite it")
    parent = f'plugins."{plugin}".containerd.runtimes."{handler}"'
    candidate = (
        text
        + f"""
[{parent}]
runtime_type = "io.containerd.runc.v2"
base_runtime_spec = "/etc/containerd/cri-base.json"
[{parent}.options]
BinaryName = "/usr/bin/crun"
SystemdCgroup = true
"""
    )
    tomllib.loads(candidate)
    return candidate


def handler_ready(node_status, handler):
    return any(
        entry.get("name") == handler and entry.get("features", {}).get("userNamespaces") is True
        for entry in node_status.get("runtimeHandlers", [])
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path(".local-factory/proc-fixed-kind"))
    args = parser.parse_args()
    settings = json.loads((args.state / "settings.json").read_text())
    if settings.get("provider") != "podman" or settings.get("rootful") is not True:
        raise SystemExit("Expected a saved rootful Podman harness")
    cluster = settings["cluster"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", cluster):
        raise SystemExit("Invalid cluster name")
    lock = (args.state / "runtime-repair.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    subprocess.run(["sudo", "-n", "true"], check=True)
    output = Path(tempfile.mkdtemp(prefix="crun-probe.", dir=args.state))
    handler = "factory-crun-" + output.name.split(".", 1)[1].replace("_", "-")
    node = cluster + "-control-plane"
    runtime = ["sudo", "-n", "podman"]
    inside = runtime + ["exec", node]
    kube = ["kubectl", "--kubeconfig", str(args.state / "kubeconfig")]
    changed = created_class = created_pod = passed = False

    def run(command, **kwargs):
        return subprocess.run(command, check=True, text=True, capture_output=True, **kwargs).stdout

    def snapshot(command, name):
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        (output / name).write_text(result.stdout + result.stderr)

    print(f"Diagnostics: {output}", flush=True)
    try:
        original = run(inside + ["cat", "/etc/containerd/config.toml"])
        (output / "original.toml").write_text(original)
        (output / "candidate.toml").write_text(crun_config(original, handler))
        # Install only in the disposable node; the default runtime remains runc.
        snapshot(inside + ["sh", "-ec", "command -v crun || true"], "existing-crun.txt")
        if not (output / "existing-crun.txt").read_text().strip():
            result = subprocess.run(
                inside
                + [
                    "sh",
                    "-ec",
                    "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends crun",
                ],
                text=True,
                capture_output=True,
                timeout=240,
                check=False,
            )
            (output / "install.log").write_text(result.stdout + result.stderr)
            result.check_returncode()
        (output / "version.txt").write_text(run(inside + ["/usr/bin/crun", "--version"]))
        # crun uses sd_bus_default_system for systemd cgroups; unlike runc it
        # does not connect directly to /run/systemd/private. kind's minimal node
        # may have systemd but no system D-Bus daemon/socket.
        snapshot(inside + ["sh", "-ec", "command -v dbus-daemon || true"], "existing-dbus.txt")
        if not (output / "existing-dbus.txt").read_text().strip():
            result = subprocess.run(
                inside
                + [
                    "sh",
                    "-ec",
                    "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends dbus",
                ],
                text=True,
                capture_output=True,
                timeout=240,
                check=False,
            )
            (output / "install-dbus.log").write_text(result.stdout + result.stderr)
            result.check_returncode()
        (output / "system-bus.txt").write_text(
            run(
                inside
                + [
                    "sh",
                    "-ec",
                    "systemctl start dbus.socket dbus.service; busctl --system --no-pager status org.freedesktop.systemd1",
                ]
            )
        )
        changed = True
        run(runtime + ["cp", str(output / "candidate.toml"), node + ":/etc/containerd/config.toml"])
        run(
            inside
            + [
                "sh",
                "-ec",
                "chown 0:0 /etc/containerd/config.toml; chmod 0644 /etc/containerd/config.toml; systemctl restart containerd",
            ]
        )
        registration_deadline = time.monotonic() + 90
        while time.monotonic() < registration_deadline:
            status = json.loads(run(kube + ["get", "node", node, "-o", "json"]))["status"]
            (output / "runtime-handlers.json").write_text(
                json.dumps(status.get("runtimeHandlers", []), indent=2)
            )
            if handler_ready(status, handler):
                break
            time.sleep(3)
        else:
            raise RuntimeError(
                "Kubelet did not advertise user-namespace support for the new handler"
            )
        resource = {
            "apiVersion": "node.k8s.io/v1",
            "kind": "RuntimeClass",
            "metadata": {"name": handler},
            "handler": handler,
        }
        run(kube + ["create", "-f", "-"], input=json.dumps(resource))
        created_class = True
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": handler, "namespace": "factory-harness"},
            "spec": {
                "runtimeClassName": handler,
                "hostUsers": False,
                "automountServiceAccountToken": False,
                "restartPolicy": "Never",
                "activeDeadlineSeconds": 90,
                "securityContext": {"runAsUser": 10001, "runAsGroup": 10001},
                "containers": [
                    {
                        "name": "factory",
                        "image": "localhost/factory-review-runner:review",
                        "imagePullPolicy": "Never",
                        "command": [
                            "bash",
                            "-ec",
                            "rootlesskit --net=host --copy-up=/etc --copy-up=/run unshare --mount --pid --fork --mount-proc sh -ec 'test -r /proc/1/status'; podman unshare cat /proc/self/uid_map; echo CRUN_PROBE_PASSED",
                        ],
                        "securityContext": {
                            "privileged": False,
                            "allowPrivilegeEscalation": True,
                            "procMount": "Unmasked",
                            "seccompProfile": {"type": "Unconfined"},
                            "appArmorProfile": {"type": "Unconfined"},
                        },
                    }
                ],
            },
        }
        (output / "pod.json").write_text(json.dumps(pod, indent=2))
        run(kube + ["create", "-f", "-"], input=json.dumps(pod))
        created_pod = True
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            value = json.loads(
                run(kube + ["-n", "factory-harness", "get", "pod", handler, "-o", "json"])
            )
            (output / "pod-status.json").write_text(json.dumps(value, indent=2))
            phase = value["status"].get("phase")
            if phase == "Succeeded":
                passed = True
                break
            if phase == "Failed":
                raise RuntimeError("Pod failed; see saved status and events")
            time.sleep(3)
        if not passed:
            raise RuntimeError("Pod did not complete before timeout")
        (output / "result.json").write_text(json.dumps({"passed": True, "runtimeClass": handler}))
        (args.state / "runtime-class").write_text(handler + "\n")
        print(f"Sandbox, private proc mount and UID mapping passed with RuntimeClass {handler}.")
        print(
            "This does not yet qualify the full Jenkins build. RuntimeClass retained for that next test."
        )
    except subprocess.CalledProcessError as error:
        (output / "command-error.txt").write_text(str(error) + "\n" + (error.stderr or ""))
        raise
    finally:
        if created_pod:
            snapshot(
                kube
                + [
                    "-n",
                    "factory-harness",
                    "get",
                    "events",
                    "--field-selector",
                    f"involvedObject.name={handler}",
                    "-o",
                    "json",
                ],
                "events.json",
            )
            snapshot(kube + ["-n", "factory-harness", "logs", handler], "probe.log")
            snapshot(
                kube + ["-n", "factory-harness", "delete", "pod", handler, "--wait=false"],
                "delete.log",
            )
        if not passed:
            (output / "result.json").write_text('{"passed":false}\n')
            if created_class:
                snapshot(kube + ["delete", "runtimeclass", handler], "delete-class.log")
            if changed:
                try:
                    run(
                        runtime
                        + [
                            "cp",
                            str(output / "original.toml"),
                            node + ":/etc/containerd/config.toml",
                        ]
                    )
                    run(
                        inside
                        + [
                            "sh",
                            "-ec",
                            "chown 0:0 /etc/containerd/config.toml; chmod 0644 /etc/containerd/config.toml; systemctl restart containerd",
                        ]
                    )
                    print(
                        "Restored original containerd configuration; installed crun/D-Bus packages and the node's D-Bus service remain available."
                    )
                except subprocess.CalledProcessError as error:
                    (output / "rollback-error.txt").write_text(
                        str(error) + "\n" + (error.stderr or "")
                    )
                    print(
                        f"ROLLBACK FAILED: restore {output / 'original.toml'} to the node config and restart containerd."
                    )
        print(f"Diagnostics: {output}")


if __name__ == "__main__":
    main()
