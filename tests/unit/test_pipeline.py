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

    def test_fcs_is_required_by_gate(self) -> None:
        jenkinsfile = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
        self.assertIn("GATE: ['BUILD', 'SBOM', 'FCS', 'COMPLIANCE', 'TEST']", jenkinsfile)
        self.assertIn("fcsArtifact", jenkinsfile)

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
        self.assertIn("catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE')", jenkinsfile)

        policy = (ROOT / "policies/rego/factory/release/release.rego").read_text(encoding="utf-8")
        self.assertIn("input.fcs.assessmentPassed", policy)
        self.assertNotIn("input.findings", policy)

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
