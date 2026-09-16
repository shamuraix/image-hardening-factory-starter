import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.catalog import load_catalog
from factory.pipeline import render_plan

ROOT = Path(__file__).resolve().parents[2]


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.images = load_catalog(ROOT / "catalog/images")

    def test_ubi9_plan_contains_parallel_application_wave(self) -> None:
        plan = render_plan(self.images, {"ubi9-minimal"})
        self.assertEqual(plan["waves"][0], ["ubi9-minimal"])
        self.assertEqual(
            plan["waves"][1],
            ["bitbucket-lts", "confluence-lts", "jira-lts"],
        )
        self.assertNotIn("ubi10-minimal", {image["name"] for image in plan["images"]})

    def test_application_records_selected_base_dependency(self) -> None:
        plan = render_plan(self.images, {"ubi9-minimal"})
        jira = next(image for image in plan["images"] if image["name"] == "jira-lts")
        self.assertEqual(jira["dependsOn"], ["ubi9-minimal"])

    def test_single_application_does_not_force_unchanged_base_build(self) -> None:
        plan = render_plan(self.images, {"jira-lts"})
        self.assertEqual([image["name"] for image in plan["images"]], ["jira-lts"])
        self.assertEqual(plan["images"][0]["dependsOn"], [])

    def test_plan_rejects_unknown_images(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown changed images"):
            render_plan(self.images, {"unknown-image"})

    def test_gate_requires_build_evidence_and_selects_assessment(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        self.assertIn("GATE: ['BUILD', 'SBOM', 'COMPLIANCE', 'TEST']", jenkinsfile)
        self.assertIn("fcsArtifact", jenkinsfile)

    def test_konflux_concept_stages_have_dependencies(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        self.assertIn("HELMPER: ['PREPARE']", jenkinsfile)
        self.assertIn("COPA: ['BUILD', 'SBOM']", jenkinsfile)
        self.assertIn("HUMMINGBIRD: ['BUILD', 'SBOM']", jenkinsfile)

    def test_concept_stage_parameters_are_exposed(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        self.assertIn("FACTORY_ENABLE_HELMPER", jenkinsfile)
        self.assertIn("FACTORY_ENABLE_COPA", jenkinsfile)
        self.assertIn("FACTORY_ENABLE_HUMMINGBIRD", jenkinsfile)

    def test_helmper_hook_writes_skipped_status_without_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            result = subprocess.run(
                [
                    str(ROOT / "scripts/helmper_inventory.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            status = json.loads((work / "evidence/helmper/status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "skipped")
            self.assertIn("context directory is missing", status["reason"])

    def test_helmper_hook_writes_skipped_status_without_charts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            (work / "context").mkdir(parents=True)
            result = subprocess.run(
                [
                    str(ROOT / "scripts/helmper_inventory.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            status = json.loads((work / "evidence/helmper/status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "skipped")
            self.assertIn("no helm charts were found", status["reason"])

    def test_helmper_hook_handles_command_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            (work / "context/chart").mkdir(parents=True)
            (work / "context/chart/Chart.yaml").write_text("name: demo\n", encoding="utf-8")
            env = os.environ.copy()
            env["FACTORY_HELMPER_COMMAND"] = (
                'test -d "${FACTORY_HELMPER_CONTEXT}" && test -s "${FACTORY_HELMPER_CHARTS_FILE}"'
            )
            success = subprocess.run(
                [
                    str(ROOT / "scripts/helmper_inventory.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(success.returncode, 0)
            status = json.loads((work / "evidence/helmper/status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "completed")

            env["FACTORY_HELMPER_COMMAND"] = "exit 9"
            failure = subprocess.run(
                [
                    str(ROOT / "scripts/helmper_inventory.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(failure.returncode, 1)
            failed_status = json.loads(
                (work / "evidence/helmper/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed_status["status"], "failed")

    def test_copa_hook_writes_skipped_status_without_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir(parents=True)
            result = subprocess.run(
                [
                    str(ROOT / "scripts/copacetic_patch_plan.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            status = json.loads(
                (work / "evidence/copacetic/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "skipped")

    def test_copa_hook_handles_command_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            (work / "evidence").mkdir(parents=True)
            (work / "evidence/findings.json").write_text('{"findings":[]}')
            env = os.environ.copy()
            env["FACTORY_COPA_COMMAND"] = "true"
            success = subprocess.run(
                [
                    str(ROOT / "scripts/copacetic_patch_plan.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(success.returncode, 0)
            status = json.loads(
                (work / "evidence/copacetic/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "completed")

            env["FACTORY_COPA_COMMAND"] = "exit 4"
            failure = subprocess.run(
                [
                    str(ROOT / "scripts/copacetic_patch_plan.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(failure.returncode, 1)
            failed_status = json.loads(
                (work / "evidence/copacetic/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed_status["status"], "failed")

    def test_hummingbird_hook_reports_failure_when_required_evidence_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            (work / "evidence").mkdir(parents=True)
            result = subprocess.run(
                [
                    str(ROOT / "scripts/hummingbird_verify.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            status = json.loads(
                (work / "evidence/hummingbird/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "failed")
            self.assertIn("required image metadata or SBOM evidence is missing", status["reason"])

    def test_hummingbird_hook_handles_command_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            (work / "evidence").mkdir(parents=True)
            (work / "image-metadata.json").write_text('{"digest":"sha256:abc"}\n', encoding="utf-8")
            (work / "evidence/sbom.cdx.json").write_text(
                '{"bomFormat":"CycloneDX"}\n', encoding="utf-8"
            )

            success = subprocess.run(
                [
                    str(ROOT / "scripts/hummingbird_verify.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(success.returncode, 0)
            status = json.loads(
                (work / "evidence/hummingbird/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "completed")

            env = os.environ.copy()
            env["FACTORY_HUMMINGBIRD_COMMAND"] = "exit 3"
            failure = subprocess.run(
                [
                    str(ROOT / "scripts/hummingbird_verify.sh"),
                    str(ROOT / "catalog/images/ubi9-minimal.yaml"),
                    str(work),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(failure.returncode, 1)
            failed_status = json.loads(
                (work / "evidence/hummingbird/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed_status["status"], "failed")

    def test_fcs_receives_credentials_and_enforces_strict_digest(self) -> None:
        script = (ROOT / "scripts/fcs_scan_image.sh").read_text(encoding="utf-8")
        self.assertIn('export FCS_CLIENT_ID="${FALCON_CLIENT_ID}"', script)
        self.assertIn('export FCS_CLIENT_SECRET="${FALCON_CLIENT_SECRET}"', script)
        self.assertEqual(script.count("--strict-digest"), 2)

    def test_quarantine_import_preserves_the_scanned_digest(self) -> None:
        script = (ROOT / "scripts/import_image.sh").read_text(encoding="utf-8")
        self.assertIn("skopeo copy --preserve-digests", script)
        self.assertIn('[[ "${digest}" == "${candidate_digest}" ]]', script)

    def test_legacy_scanners_are_informational_and_fcs_is_isolated(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        self.assertIn("'FACTORY_K8S_FCS_POD_TEMPLATE'", jenkinsfile)
        self.assertIn("'FACTORY_FCS_RUNNER_IMAGE'", jenkinsfile)
        self.assertIn("catchInterruptions: false", jenkinsfile)
        self.assertIn("stageName == 'gate' ? 'FAILURE' : 'SUCCESS'", jenkinsfile)

        policy = (ROOT / "policies/rego/factory/release/release.rego").read_text(encoding="utf-8")
        self.assertIn("input.fcs.assessmentPassed", policy)
        self.assertIn('backend == "grype"', policy)

        gate_script = (ROOT / "scripts/evaluate_gate.sh").read_text(encoding="utf-8")
        self.assertIn('(.findings | type == "array")', gate_script)

    def test_attestation_job_downloads_fcs_and_all_signed_evidence(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        for artifact in (
            "importArtifact",
            "gateArtifact",
            "sbomArtifact",
            "fcsArtifact",
            "complianceArtifact",
            "testArtifact",
        ):
            self.assertIn(artifact, jenkinsfile)

    def test_signing_stage_uses_scoped_jenkins_credentials(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        self.assertIn("COSIGN_KEY_CREDENTIAL_ID", jenkinsfile)
        self.assertIn("ARTIFACTORY_SIGN_CREDENTIAL_ID", jenkinsfile)
        self.assertIn("FACTORY_K8S_SIGNING_POD_TEMPLATE", jenkinsfile)

    def test_promotion_downloads_import_identity_and_attestation(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        self.assertIn("[importArtifact, attestArtifact]", jenkinsfile)
        self.assertIn("FACTORY_PROMOTION_LOCK_PREFIX", jenkinsfile)

    def test_change_requests_cannot_publish_to_quarantine(self) -> None:
        script = (ROOT / "scripts/import_image.sh").read_text(encoding="utf-8")
        self.assertIn("FACTORY_PROTECTED_PUBLISH", script)
        self.assertIn("candidateOnly:true", script)

    def test_jenkins_uses_parameterized_kubernetes_pod_templates(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        self.assertIn("podTemplate(", jenkinsfile)
        self.assertIn("inheritFrom: podTemplateName", jenkinsfile)
