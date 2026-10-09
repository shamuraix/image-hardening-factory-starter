"""The kind harness runs the real factory Tasks with production pod settings."""

import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from tests.unit.tekton_support import documents

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests/integration/kind"

spec = importlib.util.spec_from_file_location("harness_tekton", HARNESS / "tekton.py")
driver = importlib.util.module_from_spec(spec)
sys.modules["harness_tekton"] = driver
spec.loader.exec_module(driver)


def _harness_documents() -> dict[str, dict]:
    found = {}
    for document in yaml.safe_load_all((HARNESS / "harness-pipeline.yaml").read_text()):
        found[document["metadata"]["name"]] = document
    return found


class HarnessTests(unittest.TestCase):
    def test_pipeline_uses_real_factory_tasks_and_chains_seals(self) -> None:
        harness = _harness_documents()
        pipeline = harness["harness-build"]["spec"]
        tasks = {t["name"]: t for t in pipeline["tasks"]}
        # The deliberately failing tamper probe runs in finally: a failed task
        # under tasks would stop Tekton from scheduling build, sbom, and test.
        final = {t["name"]: t for t in pipeline["finally"]}
        self.assertEqual(set(final), {"tamper-detected"})
        tasks |= final
        real = documents("Task")
        for name, entry in tasks.items():
            ref = entry["taskRef"]["name"]
            self.assertTrue(ref in real or ref in harness, f"{name} -> {ref}")
        self.assertEqual(tasks["build"]["taskRef"]["name"], "factory-rootless-build")
        self.assertEqual(tasks["test"]["taskRef"]["name"], "factory-rootless-test")

        def inputs(name: str) -> list[str]:
            return next(p["value"] for p in tasks[name]["params"] if p["name"] == "inputs")

        self.assertEqual(inputs("prepare"), ["validate=$(tasks.validate.results.seal)"])
        self.assertEqual(inputs("build"), ["prepare=$(tasks.prepare.results.seal)"])
        self.assertEqual(inputs("sbom"), ["build=$(tasks.build.results.seal)"])
        self.assertEqual(inputs("test"), ["build=$(tasks.build.results.seal)"])
        self.assertEqual(set(driver.EXPECTED), set(tasks))

    def test_prepare_marks_its_lock_as_never_releasable(self) -> None:
        script = (HARNESS / "harness-prepare.sh").read_text()
        self.assertIn("localDevelopment:true", script)
        self.assertIn("registry.access.redhat.com/ubi", script)
        importer = (ROOT / "scripts/import_image.sh").read_text()
        signer = (ROOT / "scripts/sign_and_attest.sh").read_text()
        for guard in (importer, signer):
            self.assertIn(".localDevelopment != true", guard)

    def test_build_and_test_pods_get_user_namespaces_and_the_probed_runtime_class(self) -> None:
        with TemporaryDirectory() as directory:
            state = Path(directory)
            self.assertEqual(driver.pod_template(state, "build"), {"hostUsers": False})
            self.assertEqual(driver.pod_template(state, "sbom"), {})
            (state / "runtime-class").write_text("factory-crun-test\n")
            self.assertEqual(
                driver.pod_template(state, "test"),
                {"hostUsers": False, "runtimeClassName": "factory-crun-test"},
            )
            (state / "runtime-class").write_text("name\n  hostNetwork: true")
            with self.assertRaises(ValueError):
                driver.pod_template(state, "build")

    def test_harness_hosts_need_only_the_documented_tools(self) -> None:
        up = (HARNESS / "up.sh").read_text()
        self.assertIn("required=(kubectl git python3)", up)
        self.assertIn("nerdctl --namespace k8s.io build", up)
        self.assertIn("FACTORY_CA_BUNDLE", up)
        self.assertIn("FACTORY_HARNESS_KUBECONFIG", up)
        for removed in ("skopeo", " jq ", " yq ", "openssl", "limactl"):
            self.assertNotIn(removed, up)


if __name__ == "__main__":
    unittest.main()
