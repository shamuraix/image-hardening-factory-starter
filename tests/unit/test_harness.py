"""The local harness runs the production Tasks, with the production stage
commands and pod settings, on a Kubernetes 1.28+ cluster (docs/local-harness.md)."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

import yaml

from factory.tekton import BUILD_APPARMOR_ANNOTATION
from tests.unit.tekton_support import documents, pipeline

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests/integration/local"

spec = importlib.util.spec_from_file_location("harness_tekton", HARNESS / "tekton.py")
driver = importlib.util.module_from_spec(spec)
sys.modules["harness_tekton"] = driver
spec.loader.exec_module(driver)


def _harness() -> dict[str, dict]:
    document = yaml.safe_load((HARNESS / "harness-pipeline.yaml").read_text(encoding="utf-8"))
    assert document["kind"] == "List"
    return {item["metadata"]["name"]: item for item in document["items"]}


def _tasks(name: str) -> dict[str, dict]:
    spec = _harness()[name]["spec"]
    return {t["name"]: t for t in spec["tasks"] + spec.get("finally", [])}


def _param(entry: dict, name: str):
    return next(p["value"] for p in entry["params"] if p["name"] == name)


class HarnessPipelineTests(unittest.TestCase):
    def test_every_task_is_a_production_task_except_the_two_stand_ins(self) -> None:
        real = documents("Task")
        for name in ("harness-build", "harness-build-app"):
            for task, entry in _tasks(name).items():
                ref = entry["taskRef"]["name"]
                if ref == "harness-source":
                    continue
                self.assertIn(ref, real, f"{name}/{task}")
            prepare = _tasks(name)["prepare"]
            self.assertIn("tests/integration/local/harness-prepare.sh", _param(prepare, "command"))

    def test_stage_commands_and_seal_chain_match_production(self) -> None:
        production = {t["name"]: t for t in pipeline("factory-image-build")["spec"]["tasks"]}
        for name in ("harness-build", "harness-build-app"):
            harness = _tasks(name)
            for stage in ("validate", "context-check", "sbom", "image-check"):
                for key in ("command", "outputs", "inputs"):
                    try:
                        want = _param(production[stage], key)
                    except StopIteration:
                        continue
                    self.assertEqual(_param(harness[stage], key), want, f"{name}/{stage}/{key}")
            self.assertEqual(
                _param(harness["build"], "inputs"), _param(production["build"], "inputs")
            )
            self.assertEqual(
                _param(harness["prepare"], "inputs"), _param(production["prepare"], "inputs")
            )
            self.assertEqual(harness["build"]["taskRef"]["name"], "factory-rootless-build")

    def test_application_pipeline_builds_and_verifies_its_base_first(self) -> None:
        app = _tasks("harness-build-app")
        self.assertEqual(_param(app["base-prepare"], "image"), "$(params.base-image)")
        self.assertEqual(_param(app["base-build"], "image"), "$(params.base-image)")
        self.assertEqual(
            _param(app["base-build"], "inputs"), ["prepare=$(tasks.base-prepare.results.seal)"]
        )
        # The application's prepare receives the base build's seal and verifies
        # it in harness-prepare.sh before copying the archive.
        self.assertIn(
            "HARNESS_BASE_SEAL=$(tasks.base-build.results.seal)", _param(app["prepare"], "command")
        )
        script = (HARNESS / "harness-prepare.sh").read_text(encoding="utf-8")
        self.assertIn('artifacts.sh verify "build=${HARNESS_BASE_SEAL}"', script)

    def test_driver_expectations_cover_exactly_the_pipeline_tasks(self) -> None:
        self.assertEqual(set(driver.BASE_EXPECTED), set(_tasks("harness-build")))
        self.assertEqual(set(driver.APP_EXPECTED), set(_tasks("harness-build-app")))
        self.assertEqual(driver.BASE_EXPECTED["tamper-detected"], "False")

    def test_runs_use_the_production_pod_settings_for_kubernetes_1_28(self) -> None:
        base = driver.manifest("ubi10-minimal", 600)["spec"]
        app = driver.manifest("jira-lts", 600)["spec"]
        self.assertEqual(base["pipelineRef"]["name"], "harness-build")
        self.assertEqual(app["pipelineRef"]["name"], "harness-build-app")
        self.assertIn({"name": "base-image", "value": "ubi10-minimal"}, app["params"])
        annotated = {s["pipelineTaskName"] for s in app["taskRunSpecs"]}
        self.assertEqual(annotated, {"build", "base-build"})
        for run in (base, app):
            for spec in run["taskRunSpecs"]:
                self.assertEqual(
                    spec["metadata"]["annotations"], {BUILD_APPARMOR_ANNOTATION: "unconfined"}
                )
            text = json.dumps(run)
            # Nothing the 1.28 scope excludes (docs/scope.md).
            for excluded in ("hostUsers", "procMount", "appArmorProfile", "runtimeClassName"):
                self.assertNotIn(excluded, text)
            self.assertFalse(run["taskRunTemplate"]["podTemplate"]["automountServiceAccountToken"])
        harness_text = (HARNESS / "harness-pipeline.yaml").read_text(encoding="utf-8")
        for excluded in ("hostUsers", "procMount", "factory-rootless-test", "podman"):
            self.assertNotIn(excluded, harness_text)


class HarnessPrepareTests(unittest.TestCase):
    script = (HARNESS / "harness-prepare.sh").read_text(encoding="utf-8")

    def test_lock_is_never_releasable(self) -> None:
        self.assertIn("localDevelopment:true", self.script)
        for guard in ("scripts/import_image.sh", "scripts/sign_and_attest.sh"):
            self.assertIn(".localDevelopment != true", (ROOT / guard).read_text(encoding="utf-8"))

    def test_resources_are_https_only_checksummed_and_stay_in_the_context(self) -> None:
        self.assertIn("--proto '=https' --proto-redir '=https'", self.script)
        self.assertIn("sha256|sha512) ;;", self.script)
        self.assertIn('"${algorithm}sum" --check --status', self.script)
        self.assertIn("^[A-Za-z0-9._+-]+$", self.script)

    def test_up_enforces_the_production_platform_floor(self) -> None:
        up = (HARNESS / "up.sh").read_text(encoding="utf-8")
        self.assertIn("(1, 28)", up)
        self.assertIn("containerd://", up)
        self.assertIn("FACTORY_HARNESS_KUBECONFIG:?", up)


if __name__ == "__main__":
    unittest.main()
