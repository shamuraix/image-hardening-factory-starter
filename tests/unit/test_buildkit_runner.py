import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/run_buildkit.sh"

FAKE_TOOL = """#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
log = Path(os.environ["MOCK_LOG"])
def record(event):
    with log.open("a") as output:
        output.write(json.dumps(event) + "\\n")
if tool == "id":
    print("0" if os.environ.get("MOCK_ROOT") or os.environ.get("ROOTLESSKIT_STATE_DIR") else "10001")
elif tool == "rootlesskit":
    record([tool, args])
    os.environ["ROOTLESSKIT_STATE_DIR"] = "/mock/userns"
    while args[0].startswith("--"):
        args.pop(0)
    os.execv(args[0], args)
elif tool == "buildkitd":
    record([tool, args])
    if os.environ.get("MOCK_DAEMON_FAIL"):
        print("mock daemon startup failed", flush=True)
        sys.exit(7)
    def stop(signum, frame):
        record(["daemon-stopped"])
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    Path(str(log) + ".ready").touch()
    while True:
        time.sleep(0.01)
elif tool == "buildctl":
    if "debug" in args:
        sys.exit(0 if Path(str(log) + ".ready").exists() else 1)
    record([tool, args])
    if os.environ.get("MOCK_BUILD_BLOCK"):
        while True:
            time.sleep(0.01)
    sys.exit(int(os.environ.get("MOCK_BUILD_STATUS", "0")))
elif tool == "sleep":
    time.sleep(0.01)
"""


class BuildkitRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.log = self.directory / "commands.jsonl"
        for name in ("id", "rootlesskit", "buildkitd", "buildctl", "buildkit-runc", "sleep"):
            executable = self.directory / name
            executable.write_text(FAKE_TOOL)
            executable.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.directory}:{os.environ['PATH']}",
            "MOCK_LOG": str(self.log),
            "FACTORY_BUILDKIT_NO_PROCESS_SANDBOX": "false",
            "FACTORY_BUILD_NETWORK": "default",
            "BUILDKIT_HOST": "tcp://untrusted.invalid:1234",
        }

    def events(self) -> list:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_build(self, **settings: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(LAUNCHER), "build", "--frontend", "dockerfile.v0"],
            env={**self.env, **settings},
            text=True,
            capture_output=True,
            timeout=10,
        )

    def assert_cleaned(self) -> None:
        events = self.events()
        daemon = next(event[1] for event in events if event[0] == "buildkitd")
        state = Path(daemon[daemon.index("--root") + 1]).parent
        self.assertFalse(state.exists(), str(state))
        self.assertIn(["daemon-stopped"], events)

    def test_isolated_native_worker_and_cleanup(self) -> None:
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        daemon = next(event[1] for event in events if event[0] == "buildkitd")
        self.assertIn("--rootless", daemon)
        self.assertIn("--oci-worker-snapshotter=native", daemon)
        self.assertIn("--oci-worker-binary=buildkit-runc", daemon)
        self.assertIn("--containerd-worker=false", daemon)
        self.assertNotIn("--oci-worker-no-process-sandbox", daemon)
        self.assertNotIn("--allow-insecure-entitlement", daemon)
        rootless = next(event[1] for event in events if event[0] == "rootlesskit")
        self.assertIn("--pidns", rootless)
        client = next(event[1] for event in events if event[0] == "buildctl")
        self.assertTrue(client[1].startswith("unix:///tmp/factory-buildkit."))
        self.assertNotIn(self.env["BUILDKIT_HOST"], client)
        self.assert_cleaned()

    def test_kubernetes_mode_and_explicit_local_host_network(self) -> None:
        result = self.run_build(
            FACTORY_BUILDKIT_NO_PROCESS_SANDBOX="true", FACTORY_BUILD_NETWORK="host"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        daemon = next(event[1] for event in self.events() if event[0] == "buildkitd")
        self.assertIn("--oci-worker-no-process-sandbox", daemon)
        self.assertIn("network.host", daemon)
        rootless = next(event[1] for event in self.events() if event[0] == "rootlesskit")
        self.assertNotIn("--pidns", rootless)
        self.assert_cleaned()

    def test_build_failure_is_preserved_and_cleaned(self) -> None:
        result = self.run_build(MOCK_BUILD_STATUS="42")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assert_cleaned()

    def test_daemon_failure_reports_diagnostics_and_removes_state(self) -> None:
        result = self.run_build(MOCK_DAEMON_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("mock daemon startup failed", result.stderr)
        daemon = next(event[1] for event in self.events() if event[0] == "buildkitd")
        self.assertFalse(Path(daemon[daemon.index("--root") + 1]).parent.exists())
        self.assertFalse(any(event[0] == "buildctl" for event in self.events()))

    def test_termination_stops_client_and_daemon(self) -> None:
        process = subprocess.Popen(
            [str(LAUNCHER), "build"],
            env={**self.env, "MOCK_BUILD_BLOCK": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(200):
                if self.log.exists() and any(event[0] == "buildctl" for event in self.events()):
                    break
                time.sleep(0.01)
            else:
                self.fail("mock build never started")
            process.terminate()
            _, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 143, stderr)
            self.assert_cleaned()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_invalid_configuration_and_root_are_rejected(self) -> None:
        for settings in (
            {"MOCK_ROOT": "1"},
            {"FACTORY_BUILD_NETWORK": "slirp4netns"},
            {"FACTORY_BUILDKIT_NO_PROCESS_SANDBOX": "yes"},
        ):
            with self.subTest(settings=settings):
                result = self.run_build(**settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def test_kubernetes_template_avoids_host_privileges(self) -> None:
        pod = yaml.safe_load((ROOT / "toolchain/jenkins-buildkit-pod.yaml").read_text())
        spec = pod["spec"]
        self.assertFalse(spec["hostNetwork"])
        self.assertFalse(spec["hostPID"])
        self.assertFalse(spec["hostIPC"])
        self.assertFalse(spec["shareProcessNamespace"])
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertFalse(any("hostPath" in volume for volume in spec["volumes"]))
        security = spec["containers"][0]["securityContext"]
        self.assertFalse(security["privileged"])
        self.assertTrue(security["runAsNonRoot"])
        self.assertTrue(security["allowPrivilegeEscalation"])
        self.assertNotIn("add", security.get("capabilities", {}))
