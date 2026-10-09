"""The offline security-data bundle is built on a schedule, verified in prepare,
and sealed separately for the stages that read it."""

import unittest

import yaml

from tests.unit.tekton_support import ROOT, param, pipeline_runs, pipeline_task, task


def _inputs(name: str) -> list[str]:
    return param(pipeline_task("factory-image-build", name), "inputs")


class SecurityDataTests(unittest.TestCase):
    def test_scan_and_compliance_verify_the_security_data_seal(self) -> None:
        seal = "security-data=$(tasks.prepare.results.security-data-seal)"
        for stage in ("scan", "compliance"):
            with self.subTest(stage=stage):
                self.assertIn(seal, _inputs(stage))
        prepare = task("factory-prepare")
        self.assertIn("security-data-seal", {r["name"] for r in prepare["spec"]["results"]})
        run = next(s for s in prepare["spec"]["steps"] if s["name"] == "run")
        self.assertIn("scripts/fetch_security_bundle.sh", run["script"])

    def test_bundle_refresh_is_scheduled_on_the_default_branch_only(self) -> None:
        run = pipeline_runs()["security-data-on-schedule"]
        annotations = run["metadata"]["annotations"]
        self.assertEqual(annotations["pipelinesascode.tekton.dev/on-event"], "[incoming]")
        self.assertEqual(run["spec"]["pipelineRef"]["name"], "factory-security-data")
        accounts = {
            s["pipelineTaskName"]: s["serviceAccountName"] for s in run["spec"]["taskRunSpecs"]
        }
        self.assertEqual(accounts["security-data"], "factory-intake")
        schedules = list(
            yaml.safe_load_all((ROOT / "deploy/base/schedules.yaml").read_text(encoding="utf-8"))
        )
        triggered = {
            arg
            for doc in schedules
            if doc["kind"] == "CronJob"
            for arg in doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0][
                "args"
            ]
        }
        self.assertIn("security-data-on-schedule", triggered)

    def test_signing_key_and_write_token_live_in_separate_steps(self) -> None:
        steps = {s["name"]: s for s in task("factory-security-data")["spec"]["steps"]}
        build_secrets = {
            e["valueFrom"]["secretKeyRef"]["name"]
            for e in steps["build"]["env"]
            if "valueFrom" in e
        }
        publish_secrets = {
            e["valueFrom"]["secretKeyRef"]["name"]
            for e in steps["publish"]["env"]
            if "valueFrom" in e
        }
        self.assertEqual(build_secrets, {"factory-intake-cosign"})
        self.assertEqual(publish_secrets, {"factory-artifactory-intake"})

    def test_pointer_is_published_after_the_archive_and_fetch_checks_everything(self) -> None:
        publish = (ROOT / "scripts/publish_security_bundle.sh").read_text(encoding="utf-8")
        self.assertLess(
            publish.index('--upload-file "${file}"'), publish.index('"${base}/current.json"')
        )
        fetch = (ROOT / "scripts/fetch_security_bundle.sh").read_text(encoding="utf-8")
        for check in ("digest mismatch", "cosign verify-blob", "sha256sum --check --strict"):
            self.assertIn(check, fetch)


if __name__ == "__main__":
    unittest.main()
