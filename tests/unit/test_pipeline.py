import unittest
from pathlib import Path

from factory.catalog import load_catalog
from factory.pipeline import render_pipeline

ROOT = Path(__file__).resolve().parents[2]


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.images = load_catalog(ROOT / "catalog/images")

    def test_ubi9_pipeline_contains_parallel_application_builds(self) -> None:
        pipeline = render_pipeline(self.images, {"ubi9-minimal"})
        self.assertIn("build:bitbucket-lts", pipeline)
        self.assertIn("build:confluence-lts", pipeline)
        self.assertIn("build:jira-lts", pipeline)
        self.assertNotIn("build:ubi10-minimal", pipeline)

    def test_application_prepare_waits_for_changed_base_import(self) -> None:
        pipeline = render_pipeline(self.images, {"ubi9-minimal"})
        needs = {item["job"] for item in pipeline["prepare:jira-lts"]["needs"]}
        self.assertEqual(needs, {"validate:jira-lts", "import:ubi9-minimal"})

    def test_single_application_does_not_force_unchanged_base_build(self) -> None:
        pipeline = render_pipeline(self.images, {"jira-lts"})
        self.assertIn("build:jira-lts", pipeline)
        self.assertNotIn("build:ubi9-minimal", pipeline)

    def test_ai_patch_job_does_not_gate_import(self) -> None:
        pipeline = render_pipeline(self.images, {"jira-lts"})
        import_needs = {item["job"] for item in pipeline["import:jira-lts"]["needs"]}
        self.assertNotIn("patch-mr:jira-lts", import_needs)

    def test_fcs_is_required_by_gate(self) -> None:
        pipeline = render_pipeline(self.images, {"jira-lts"})
        self.assertIn("fcs:jira-lts", pipeline)
        gate_needs = {item["job"] for item in pipeline["gate:jira-lts"]["needs"]}
        self.assertIn("fcs:jira-lts", gate_needs)
        self.assertIn("build:jira-lts", gate_needs)

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
        component = (ROOT / "components/jobs.yml").read_text(encoding="utf-8")
        self.assertIn(".factory-scan:", component)
        self.assertIn("allow_failure: true", component)
        self.assertIn("factory-fcs-runner:${FCS_CLI_VERSION}", component)
        self.assertIn("tags: [factory-fcs-connected]", component)

        policy = (ROOT / "policies/rego/factory/release/release.rego").read_text(encoding="utf-8")
        self.assertIn("input.fcs.assessmentPassed", policy)
        self.assertNotIn("input.findings", policy)

        gate_script = (ROOT / "scripts/evaluate_gate.sh").read_text(encoding="utf-8")
        self.assertIn('(.findings | type == "array")', gate_script)

    def test_attestation_job_downloads_fcs_and_all_signed_evidence(self) -> None:
        pipeline = render_pipeline(self.images, {"jira-lts"})
        attest_needs = {item["job"] for item in pipeline["attest:jira-lts"]["needs"]}
        self.assertEqual(
            attest_needs,
            {
                "import:jira-lts",
                "gate:jira-lts",
                "sbom:jira-lts",
                "fcs:jira-lts",
                "compliance:jira-lts",
                "test:jira-lts",
            },
        )

    def test_signing_component_uses_artifactory_oidc(self) -> None:
        component = (ROOT / "components/jobs.yml").read_text(encoding="utf-8")
        self.assertIn("ARTIFACTORY_ID_TOKEN", component)

    def test_promotion_downloads_import_identity_and_attestation(self) -> None:
        pipeline = render_pipeline(self.images, {"jira-lts"})
        promote_needs = {item["job"] for item in pipeline["promote:jira-lts"]["needs"]}
        self.assertEqual(promote_needs, {"import:jira-lts", "attest:jira-lts"})
